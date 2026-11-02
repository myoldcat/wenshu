"""Dedicated offline Qwen3 scoring process; independent from app/worker state.

The HTTP parent never imports torch. A single lazy child owns CUDA and is
terminated after an idle interval or request deadline, returning its GPU memory.
"""
from __future__ import annotations

import gc
import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import threading
import time

MODEL_REVISION = '22e683669bc0f0bd69640a1354a6d0aebcfeede5'
MODEL_ID = 'Qwen/Qwen3-Reranker-4B'
TEMPLATE_VERSION = 'qwen-official-transformers-parts-1'
DEFAULT_INSTRUCTION = 'Given a web search query, retrieve relevant passages that answer the query'
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
MAX_LENGTH = 4096
MAX_DOCUMENTS = 1000
MAX_BODY_BYTES = 32 * 1024 * 1024
MAX_QUERY_CHARS = 6000
MAX_DOCUMENT_CHARS = 24000
MAX_REQUEST_SECONDS = 120

# Official revision metadata pins the read-only model files below.
# Read-only weight mounts and an offline loader keep the exact version fixed.
MODEL_FILES = {
    'config.json': (727, '38bff5eac700032a185745e4076eccad7aa453473cafc2a27de412cdb7b79e19'),
    'generation_config.json': (214, '81051cd3f6e77013827148d0b8a6ead93f8ac390d5ab805f849199f0af6a08db'),
    'merges.txt': (1671853, '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5'),
    'model-00001-of-00002.safetensors': (4058781760, 'cf2e87cbf71fa628961532232e04dd6c19702a0a057f5e2aff95ea1aca4fd488'),
    'model-00002-of-00002.safetensors': (3984833200, '78946d22b7f6456ea7a5358dbdf3982de36c5bac1f166a5fd58e18e31db8048a'),
    'model.safetensors.index.json': (32819, '18a783f197360068da36d2ff9ecffce0121542a87598fd1fef3079ead1c3cc08'),
    'special_tokens_map.json': (613, '76862e765266b85aa9459767e33cbaf13970f327a0e88d1c65846c2ddd3a1ecd'),
    'tokenizer.json': (11422654, 'aeb13307a71acd8fe81861d94ad54ab689df773318809eed3cbe794b4492dae4'),
    'tokenizer_config.json': (9706, '253153d0738ceb4c668d2eff957714dd2bea0b56de772a9fdccd96cbf517e6a0'),
    'vocab.json': (2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
}


class RuntimeFailure(Exception):
    def __init__(self, code: str, status: int = 503):
        super().__init__(code)
        self.code, self.status = code, status


def verify_model_files(model_path: Path) -> None:
    for name, (size, checksum) in MODEL_FILES.items():
        path = model_path / name
        if not path.is_file() or path.stat().st_size != size:
            raise RuntimeFailure('model_unavailable')
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
                digest.update(chunk)
        if digest.hexdigest() != checksum:
            raise RuntimeFailure('model_checksum_mismatch')


def validate_request(payload: object) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeFailure('invalid_request', 400)
    query = payload.get('query')
    if not isinstance(query, str) or not query.strip() or len(query) > MAX_QUERY_CHARS:
        raise RuntimeFailure('invalid_query', 400)
    documents = payload.get('documents')
    if not isinstance(documents, list) or not 1 <= len(documents) <= MAX_DOCUMENTS:
        raise RuntimeFailure('invalid_documents', 400)
    seen, cleaned = set(), []
    for document in documents:
        if not isinstance(document, dict):
            raise RuntimeFailure('invalid_document', 400)
        key = document.get('id')
        if not isinstance(key, str) or not key or len(key) > 256 or key in seen:
            raise RuntimeFailure('invalid_document_id', 400)
        seen.add(key)
        parts = document.get('documentParts')
        if parts is None and isinstance(document.get('document'), str):
            parts = [{'kind': 'document', 'text': document['document']}]
        if not isinstance(parts, list) or not 1 <= len(parts) <= 16:
            raise RuntimeFailure('invalid_document_parts', 400)
        normalized = []
        for part in parts:
            if not isinstance(part, dict) or not isinstance(part.get('text'), str):
                raise RuntimeFailure('invalid_document_part', 400)
            kind = part.get('kind', 'part')
            minimum = part.get('minTokens', 96)
            if not isinstance(kind, str) or len(kind) > 80 or any(c in kind for c in '\r\n'):
                raise RuntimeFailure('invalid_part_kind', 400)
            if isinstance(minimum, bool) or not isinstance(minimum, int) or not 0 <= minimum <= 96:
                raise RuntimeFailure('invalid_part_minimum', 400)
            normalized.append({'kind': kind, 'text': part['text'], 'minTokens': minimum})
        if not any(part['text'].strip() for part in normalized) or sum(len(p['text']) for p in normalized) > MAX_DOCUMENT_CHARS:
            raise RuntimeFailure('invalid_document_length', 400)
        cleaned.append({'id': key, 'documentParts': normalized})
    timeout = payload.get('timeoutSeconds', MAX_REQUEST_SECONDS)
    if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 1 <= timeout <= MAX_REQUEST_SECONDS:
        raise RuntimeFailure('invalid_timeout', 400)
    # The public internal API does not accept caller-provided model/instruction
    # overrides. All callers use the tested official scoring contract.
    return {'query': query, 'documents': cleaned, 'timeoutSeconds': float(timeout)}


def prepare_pair(tokenizer, query: str, parts: list[dict]) -> tuple[list[int], dict]:
    prefix = tokenizer.encode(PREFIX, add_special_tokens=False)
    suffix = tokenizer.encode(SUFFIX, add_special_tokens=False)
    part_ids = [tokenizer.encode(p['text'], add_special_tokens=False) for p in parts]
    markers = [f'[{p["kind"]}]\n' for p in parts]
    head = f'<Instruct>: {DEFAULT_INSTRUCTION}\n<Query>: {query}\n<Document>: '

    def render(ids):
        return '\n\n'.join(marker + tokenizer.decode(tokens, skip_special_tokens=False) for marker, tokens in zip(markers, ids))

    untruncated = len(prefix) + len(tokenizer.encode(head + render(part_ids), add_special_tokens=False)) + len(suffix)
    effective, truncated = part_ids, untruncated > MAX_LENGTH
    if truncated:
        overhead = len(prefix) + len(suffix) + len(tokenizer.encode(head + '\n\n'.join(markers), add_special_tokens=False))
        available = MAX_LENGTH - overhead - 32
        minima = [min(len(tokens), p['minTokens']) for p, tokens in zip(parts, part_ids)]
        if sum(minima) > available:
            raise RuntimeFailure('packet_exceeds_context', 400)
        demand = [len(tokens) - minimum for tokens, minimum in zip(part_ids, minima)]
        total_demand, residual = sum(demand), available - sum(minima)
        budgets = [minimum + (int(residual * need / total_demand) if total_demand else 0) for minimum, need in zip(minima, demand)]
        clip = tokenizer.encode('\n[…truncated…]\n', add_special_tokens=False)
        effective = []
        for ids, budget in zip(part_ids, budgets):
            if len(ids) <= budget:
                effective.append(ids)
            else:
                content = max(0, budget - len(clip))
                left, right = (content + 1) // 2, content // 2
                effective.append(ids[:left] + clip + (ids[-right:] if right else []))
    body = tokenizer.encode(head + render(effective), add_special_tokens=False)
    ids = prefix + body + suffix
    if len(ids) > MAX_LENGTH:
        raise RuntimeFailure('packet_exceeds_context', 400)
    return ids, {'inputTokens': len(ids), 'untruncatedTokens': untruncated, 'truncated': truncated}


def _worker_main(connection, model_path: str, min_free_mib: int):
    """All heavy imports and CUDA allocations are confined to this process."""
    try:
        started = time.monotonic()
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer
        if not torch.cuda.is_available() or torch.cuda.device_count() != 1:
            raise RuntimeFailure('gpu_not_isolated')
        free, _ = torch.cuda.mem_get_info(0)
        if free < min_free_mib * 1024 ** 2:
            raise RuntimeFailure('gpu_memory_unavailable')
        device = torch.device('cuda:0')
        tokenizer = AutoTokenizer.from_pretrained(model_path, padding_side='left', local_files_only=True, trust_remote_code=False)
        model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True, trust_remote_code=False).to(device).eval()
        no_id, yes_id = [tokenizer.convert_tokens_to_ids(word) for word in ('no', 'yes')]
        if any(tokenizer.encode(word, add_special_tokens=False) != [token] for word, token in (('no', no_id), ('yes', yes_id))):
            raise RuntimeFailure('invalid_yes_no_tokens')
        torch.cuda.synchronize()
        connection.send({'ready': True, 'loadSeconds': time.monotonic() - started})
        while True:
            request = connection.recv()
            if request is None:
                break
            rows, inference_seconds, prepare_seconds = [], 0.0, 0.0
            for document in request['documents']:
                if time.monotonic() >= request['deadline']:
                    raise RuntimeFailure('deadline_exceeded', 504)
                started = time.monotonic()
                ids, meta = prepare_pair(tokenizer, request['query'], document['documentParts'])
                prepare_seconds += time.monotonic() - started
                encoded = tokenizer.pad({'input_ids': [ids]}, padding=True, return_tensors='pt').to(device)
                torch.cuda.synchronize()
                started = time.monotonic()
                with torch.inference_mode():
                    logits = model(**encoded, use_cache=False, logits_to_keep=1).logits[:, -1, :]
                    selected = torch.stack([logits[:, no_id].float(), logits[:, yes_id].float()], dim=1)
                    score = float(torch.softmax(selected, dim=1)[0, 1].cpu())
                    raw = selected[0].cpu().tolist()
                torch.cuda.synchronize()
                inference_seconds += time.monotonic() - started
                if not math.isfinite(score):
                    raise RuntimeFailure('nonfinite_score')
                rows.append({'id': document['id'], 'score': score, 'logitDiff': raw[1] - raw[0], **meta})
            connection.send({'scores': rows, 'timing': {'inferenceSeconds': inference_seconds, 'prepareSeconds': prepare_seconds}})
    except RuntimeFailure as exc:
        connection.send({'error': exc.code, 'status': exc.status})
    except (EOFError, BrokenPipeError):
        pass
    except Exception:
        # Never include candidate text, raw exception paths, or model contents
        # in transport errors or logs.
        try:
            connection.send({'error': 'inference_failed', 'status': 503})
        except (EOFError, BrokenPipeError, OSError):
            pass
    finally:
        connection.close()
        gc.collect()


class RuntimeManager:
    def __init__(self, model_path: Path, *, idle_seconds: float = 60, min_free_mib: int = 10000, context=None, clock=time.monotonic):
        self.model_path = Path(model_path)
        self.idle_seconds, self.min_free_mib = idle_seconds, min_free_mib
        self.context = context or multiprocessing.get_context('spawn')
        self.clock = clock
        self.lock = threading.Lock()
        self.process = self.connection = None
        self.worker_loaded = False
        self.last_used = 0.0
        self.verified = False

    def verify(self):
        verify_model_files(self.model_path)
        self.verified = True

    def status(self):
        process = self.process
        try:
            alive = bool(process and process.is_alive())
        except ValueError:  # A concurrent idle/timeout reap may close its handle.
            alive = False
        return {'status': 'ok' if self.verified else 'unavailable', 'ready': self.verified,
                'loaded': self.worker_loaded and alive, 'busy': self.lock.locked(),
                'model': MODEL_ID, 'modelRevision': MODEL_REVISION, 'templateVersion': TEMPLATE_VERSION,
                'maxLength': MAX_LENGTH, 'idleUnloadSeconds': self.idle_seconds}

    def _stop(self):
        process, connection = self.process, self.connection
        self.process = self.connection = None
        self.worker_loaded = False
        if connection:
            connection.close()
        if process:
            if process.is_alive():
                process.terminate()
            process.join(timeout=2)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            process.close()

    def close(self):
        with self.lock:
            self._stop()

    def unload_if_idle(self):
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if self.process and self.clock() - self.last_used >= self.idle_seconds:
                self._stop()
                return True
            return False
        finally:
            self.lock.release()

    def _receive(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0 or not self.connection.poll(remaining):
            raise RuntimeFailure('deadline_exceeded', 504)
        try:
            result = self.connection.recv()
        except (EOFError, OSError):
            raise RuntimeFailure('worker_unavailable') from None
        if 'error' in result:
            raise RuntimeFailure(result['error'], result.get('status', 503))
        return result

    def score(self, payload):
        request = validate_request(payload)
        if not self.verified:
            raise RuntimeFailure('model_unavailable')
        if not self.lock.acquire(blocking=False):
            raise RuntimeFailure('reranker_busy')
        started = self.clock()
        deadline = started + request['timeoutSeconds']
        cold, load_seconds = False, 0.0
        try:
            if not self.process or not self.process.is_alive():
                self._stop()
                parent, child = self.context.Pipe()
                self.connection = parent
                self.process = self.context.Process(target=_worker_main, args=(child, str(self.model_path), self.min_free_mib), daemon=True)
                self.process.start()
                child.close()
                ready = self._receive(deadline)
                if not ready.get('ready'):
                    raise RuntimeFailure('worker_unavailable')
                self.worker_loaded = True
                cold, load_seconds = True, ready['loadSeconds']
            self.connection.send({**request, 'deadline': deadline})
            result = self._receive(deadline)
            self.last_used = self.clock()
            result.update(model=MODEL_ID, modelRevision=MODEL_REVISION, templateVersion=TEMPLATE_VERSION, coldStart=cold)
            result['timing'].update(loadSeconds=load_seconds, wallSeconds=self.clock() - started)
            return result
        except RuntimeFailure:
            self._stop()
            raise
        except (OSError, EOFError, ValueError):
            self._stop()
            raise RuntimeFailure('worker_unavailable') from None
        finally:
            self.lock.release()
