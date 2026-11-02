"""Isolated, resumable official Qwen3 reranker scorer; never opens the app DB.

Input JSONL: topicId, sourceId, packetHash, query, document (or doc),
instruction (optional), documentParts (optional [{kind, text, minTokens}]).
Output JSONL contains raw yes/no logits and actual inference timings. Existing
matching rows are reused only when --resume is explicit and are counted as cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

DEFAULT_INSTRUCTION = 'Given a web search query, retrieve relevant passages that answer the query'
PREFIX = '<|im_start|>system\nJudge whether the Document meets the requirements based on the Query and the Instruct provided. Note that the answer can only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
SUFFIX = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
TEMPLATE_VERSION = 'qwen-official-transformers-parts-1'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def gpu_inventory():
    p = subprocess.run(['nvidia-smi', '--query-gpu=index,uuid,name,memory.total,memory.free', '--format=csv,noheader,nounits'], capture_output=True, text=True, check=True)
    rows = []
    for line in p.stdout.splitlines():
        index, uid, name, total, free = [part.strip() for part in line.split(',')]
        rows.append(dict(index=int(index), uuid=uid, name=name, totalMiB=int(total), freeMiB=int(free)))
    return rows


def argument_parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', required=True, type=Path)
    p.add_argument('--output', required=True, type=Path)
    p.add_argument('--summary', required=True, type=Path)
    p.add_argument('--model-path', required=True, type=Path)
    p.add_argument('--revision', required=True)
    p.add_argument('--gpu', type=int, default=0, help='Visible CUDA index; use Docker device selection to isolate the physical GPU')
    p.add_argument('--max-length', type=int, default=4096)
    p.add_argument('--batch-size', type=int, default=1)
    p.add_argument('--min-free-mib', type=int, default=10000)
    p.add_argument('--max-new-pairs', type=int, default=1200)
    p.add_argument('--expected-input-sha256')
    p.add_argument('--resume', action='store_true')
    p.add_argument('--repeat', type=int, default=1, help='Real repeated passes; only pass 0 may use resume cache')
    return p


def main():
    args = argument_parser().parse_args()
    if args.max_length < 256 or not 1 <= args.batch_size <= 2 or not 1 <= args.repeat <= 3:
        raise ValueError('Use max-length >=256, batch-size 1..2, repeat 1..3')
    if args.output.exists() and not args.resume:
        raise ValueError('Output exists; choose a new output or explicit --resume')
    input_sha256 = hashlib.sha256(args.input.read_bytes()).hexdigest()
    if args.expected_input_sha256 and input_sha256 != args.expected_input_sha256:
        raise RuntimeError('Frozen packet input hash changed')
    started = time.perf_counter()
    inventory = gpu_inventory()
    # nvidia-smi inside Docker can retain physical indices. CUDA_VISIBLE_DEVICES
    # still guarantees that torch sees only the single explicitly selected GPU.
    eligible = [row for row in inventory if row['freeMiB'] >= args.min_free_mib]
    if not eligible:
        raise RuntimeError(f'No visible GPU has {args.min_free_mib} MiB free; leave existing workloads unchanged')
    dependency_started = time.perf_counter()
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer
    dependency_import_seconds = time.perf_counter() - dependency_started
    cuda_started = time.perf_counter()
    if not torch.cuda.is_available() or torch.cuda.device_count() != 1 or args.gpu != 0:
        raise RuntimeError('Require exactly one isolated visible CUDA device and --gpu 0')
    free_bytes, total_bytes = torch.cuda.mem_get_info(0)
    if free_bytes < args.min_free_mib * 1024 ** 2:
        raise RuntimeError('Selected CUDA GPU has insufficient free memory; existing workloads were not touched')
    device = torch.device('cuda:0')
    torch.cuda.set_device(device)
    torch.cuda.reset_peak_memory_stats(device)
    cuda_initialization_seconds = time.perf_counter() - cuda_started
    load_started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(args.model_path, padding_side='left', local_files_only=True, trust_remote_code=False)
    model = AutoModelForCausalLM.from_pretrained(args.model_path, torch_dtype=torch.bfloat16, attn_implementation='sdpa', local_files_only=True, trust_remote_code=False).to(device).eval()
    torch.cuda.synchronize()
    load_seconds = time.perf_counter() - load_started
    no_id = tokenizer.convert_tokens_to_ids('no')
    yes_id = tokenizer.convert_tokens_to_ids('yes')
    if tokenizer.encode('no', add_special_tokens=False) != [no_id] or tokenizer.encode('yes', add_special_tokens=False) != [yes_id]:
        raise RuntimeError('yes/no must each be exactly one tokenizer token')
    prefix = tokenizer.encode(PREFIX, add_special_tokens=False)
    suffix = tokenizer.encode(SUFFIX, add_special_tokens=False)
    run_id = str(uuid.uuid4())
    config = dict(modelRevision=args.revision, templateVersion=TEMPLATE_VERSION, dtype='bfloat16', attention='sdpa', maxLength=args.max_length, batchSize=args.batch_size, transformers=transformers.__version__, torch=torch.__version__, prefixHash=digest(PREFIX), suffixHash=digest(SUFFIX), useCache=False, logitsToKeep=1)
    config_hash = digest(config)
    cache = {}
    if args.resume and args.output.exists():
        for line in args.output.read_text(encoding='utf-8').splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get('configHash') == config_hash and not row.get('error'):
                cache[row['requestHash']] = row
    requests = [json.loads(line) for line in args.input.read_text(encoding='utf-8').splitlines() if line.strip()]
    if len(requests) * args.repeat > args.max_new_pairs:
        # Do not quietly run only part of the candidate set.
        uncached = sum(digest(dict(query=row['query'], document=row.get('document', row.get('doc')), documentParts=row.get('documentParts'), instruction=row.get('instruction', DEFAULT_INSTRUCTION), packetHash=row.get('packetHash'))) not in cache for row in requests)
        if uncached + len(requests) * (args.repeat - 1) > args.max_new_pairs:
            raise RuntimeError('Explicit new-pair budget is below requested complete experiment')

    def prepare(row):
        query = row['query']
        instruction = row.get('instruction') or DEFAULT_INSTRUCTION
        parts = row.get('documentParts')
        if not parts:
            parts = [dict(kind='document', text=row.get('document', row.get('doc', '')))]
        if not isinstance(query, str) or any(not isinstance(x.get('text'), str) for x in parts):
            raise ValueError('query and document part text must be strings')
        part_ids = [tokenizer.encode(x['text'], add_special_tokens=False) for x in parts]
        markers = [f'[{x.get("kind", "part")}]\n' for x in parts]
        head = f'<Instruct>: {instruction}\n<Query>: {query}\n<Document>: '

        def render(ids):
            return '\n\n'.join(marker + tokenizer.decode(tokens, skip_special_tokens=False) for marker, tokens in zip(markers, ids))

        original_doc = render(part_ids)
        original_ids = tokenizer.encode(head + original_doc, add_special_tokens=False)
        untruncated = len(prefix) + len(original_ids) + len(suffix)
        effective = part_ids
        truncated = untruncated > args.max_length
        if truncated:
            overhead = len(prefix) + len(suffix) + len(tokenizer.encode(head + '\n\n'.join(markers), add_special_tokens=False))
            available = args.max_length - overhead - 32
            minima = [min(len(tokens), int(part.get('minTokens', 96))) for part, tokens in zip(parts, part_ids)]
            if sum(minima) > available:
                raise RuntimeError('Query and part minimums cannot fit max-length; no fragment may be silently dropped')
            lengths = [len(tokens) for tokens in part_ids]
            residual = available - sum(minima)
            demand = [length - minimum for length, minimum in zip(lengths, minima)]
            total_demand = sum(demand)
            budgets = [minimum + (int(residual * need / total_demand) if total_demand else 0) for minimum, need in zip(minima, demand)]
            # Preserve the beginning and end of each independently identified
            # excerpt. The inserted marker is explicit derived packaging text.
            clip_marker = tokenizer.encode('\n[…truncated…]\n', add_special_tokens=False)
            effective = []
            for ids, budget in zip(part_ids, budgets):
                if len(ids) <= budget:
                    effective.append(ids)
                else:
                    content = max(0, budget - len(clip_marker))
                    left = (content + 1) // 2
                    right = content // 2
                    effective.append(ids[:left] + clip_marker + (ids[-right:] if right else []))
        document = render(effective)
        body = tokenizer.encode(head + document, add_special_tokens=False)
        if len(prefix) + len(body) + len(suffix) > args.max_length:
            raise RuntimeError('Balanced token accounting exceeds max-length; refusing silent truncation')
        ids = prefix + body + suffix
        part_meta = [dict(kind=p.get('kind', 'part'), originalTokens=len(before), effectiveTokens=len(after), truncated=len(before) != len(after)) for p, before, after in zip(parts, part_ids, effective)]
        return ids, dict(inputTokens=len(ids), untruncatedTokens=untruncated, truncated=truncated, truncationMode='balanced-parts-head-tail' if truncated else 'none', effectiveDocumentHash=digest(document), effectiveParts=part_meta)

    stats = dict(runId=run_id, config=config, configHash=config_hash, inputSha256=input_sha256, requestedPairs=len(requests), repeats=args.repeat, dependencyImportSeconds=dependency_import_seconds, cudaInitializationSeconds=cuda_initialization_seconds, loadedSeconds=load_seconds, inventoryBefore=inventory, cudaFreeBeforeMiB=free_bytes / 1024 ** 2, computedPairs=0, cacheHits=0, truncatedPairs=0, inferenceSeconds=0.0, batches=[], passSeconds=[], topicPassMetrics={}, failures=0)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.summary.parent.mkdir(parents=True, exist_ok=True)

    def save_summary():
        stats.update(wallSeconds=time.perf_counter() - started, cudaPeakAllocatedMiB=torch.cuda.max_memory_allocated(device) / 1024 ** 2, cudaPeakReservedMiB=torch.cuda.max_memory_reserved(device) / 1024 ** 2)
        temporary = args.summary.with_suffix(args.summary.suffix + '.tmp')
        temporary.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, args.summary)

    with args.output.open('a', encoding='utf-8') as output:
        for pass_index in range(args.repeat):
            pass_started = time.perf_counter()
            pending = []
            for row in requests:
                request_hash = digest(dict(query=row['query'], document=row.get('document', row.get('doc')), documentParts=row.get('documentParts'), instruction=row.get('instruction', DEFAULT_INSTRUCTION), packetHash=row.get('packetHash')))
                if pass_index == 0 and request_hash in cache:
                    stats['cacheHits'] += 1
                    continue
                prepare_started = time.perf_counter()
                ids, meta = prepare(row)
                meta['prepareSeconds'] = time.perf_counter() - prepare_started
                topic_key = f'{pass_index}:{row.get("topicId", "unknown")}'
                topic_stats = stats['topicPassMetrics'].setdefault(topic_key, dict(passIndex=pass_index, topicId=row.get('topicId'), pairs=0, prepareSeconds=0.0, inferenceSeconds=0.0, processingSeconds=0.0))
                topic_stats['prepareSeconds'] += meta['prepareSeconds']
                pending.append((row, request_hash, ids, meta))
            for offset in range(0, len(pending), args.batch_size):
                processing_started = time.perf_counter()
                batch = pending[offset:offset + args.batch_size]
                encoded = tokenizer.pad({'input_ids': [item[2] for item in batch]}, padding=True, return_tensors='pt').to(device)
                torch.cuda.synchronize()
                batch_started = time.perf_counter()
                try:
                    with torch.inference_mode():
                        logits = model(**encoded, use_cache=False, logits_to_keep=1).logits[:, -1, :]
                        selected = torch.stack([logits[:, no_id].float(), logits[:, yes_id].float()], dim=1)
                        scores = torch.softmax(selected, dim=1)[:, 1].cpu().tolist()
                        raw = selected.cpu().tolist()
                    torch.cuda.synchronize()
                except Exception:
                    stats['failures'] += 1
                    save_summary()
                    raise
                seconds = time.perf_counter() - batch_started
                stats['inferenceSeconds'] += seconds
                stats['computedPairs'] += len(batch)
                stats['truncatedPairs'] += sum(item[3]['truncated'] for item in batch)
                stats['batches'].append(dict(passIndex=pass_index, count=len(batch), seconds=seconds, paddedTokens=int(encoded['input_ids'].shape[1])))
                if 'firstBatchSeconds' not in stats:
                    stats['firstBatchSeconds'] = seconds
                    stats['coldLoadPlusFirstBatchSeconds'] = load_seconds + seconds
                for (row, request_hash, ids, meta), score, (no_logit, yes_logit) in zip(batch, scores, raw):
                    result = {k: row[k] for k in ('topicId', 'sourceId', 'packetHash') if k in row}
                    result.update(meta)
                    result.update(runId=run_id, passIndex=pass_index, configHash=config_hash, requestHash=request_hash, queryHash=digest(row['query']), score=score, p_yes=score, logitDiff=yes_logit-no_logit, yesLogit=yes_logit, noLogit=no_logit, inferenceSeconds=seconds/len(batch), batchSeconds=seconds, cacheHit=False)
                    if not math.isfinite(score):
                        raise RuntimeError('Nonfinite reranker score')
                    output.write(json.dumps(result, ensure_ascii=False) + '\n')
                output.flush()
                os.fsync(output.fileno())
                processing_seconds = time.perf_counter() - processing_started
                for row, _, _, _ in batch:
                    topic_stats = stats['topicPassMetrics'][f'{pass_index}:{row.get("topicId", "unknown")}']
                    topic_stats['pairs'] += 1
                    topic_stats['inferenceSeconds'] += seconds / len(batch)
                    topic_stats['processingSeconds'] += processing_seconds / len(batch)
                if offset % 20 == 0:
                    save_summary()
                    print(json.dumps(dict(event='progress', runId=run_id, passIndex=pass_index, computed=stats['computedPairs'], cached=stats['cacheHits'], seconds=stats['inferenceSeconds'])), flush=True)
            stats['passSeconds'].append(time.perf_counter()-pass_started)
            save_summary()
    stats['inventoryAfter'] = gpu_inventory()
    save_summary()
    print(json.dumps(dict(event='complete', **{k:v for k,v in stats.items() if k not in ('batches','config')})), flush=True)


if __name__ == '__main__':
    main()
