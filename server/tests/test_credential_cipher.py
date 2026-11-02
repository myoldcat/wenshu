"""Real server-side credential storage, using only generated test credentials."""
from concurrent.futures import ThreadPoolExecutor
import builtins
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading

import pytest

from server.credential_cipher import CredentialError, FernetFileCipher
from server.model_gateway import DPAPICipher, ModelError, ModelService
from server.store import Store


SECRET = 'synthetic-test-credential-never-a-provider-key'
LOCAL = {'X-Wenshu-Client': 'local'}
POSIX_ONLY = pytest.mark.skipif(os.name != 'posix', reason='POSIX private-file credential protection')


def key_path(root):
    return root / '.secrets' / 'model.key'


def model_payload(**changes):
    return dict(name='凭证测试模型', baseUrl='http://127.0.0.1:11434/v1',
                model='synthetic-test-model', **changes)


def assert_redacted(text, *additional):
    for value in (SECRET, 'apiKey', 'key_cipher', 'fernet:v1:', *additional):
        assert value not in text


def test_default_cipher_matches_server_platform_without_creating_a_key(tmp_path):
    service = ModelService(Store(tmp_path / 'data'))
    assert isinstance(service.cipher, DPAPICipher if os.name == 'nt' else FernetFileCipher)
    assert not key_path(service.store.root).exists()
    model = service.save_config(model_payload())
    assert model['hasApiKey'] is False
    assert service.resolve(model['id'])['apiKey'] == ''
    assert not key_path(service.store.root).exists()


@POSIX_ONLY
class TestPrivateCredentialFiles:
    def test_authenticated_roundtrip_private_permissions_and_fresh_nonce(self, tmp_path):
        cipher = FernetFileCipher(tmp_path)
        first = cipher.encrypt(SECRET)
        second = cipher.encrypt(SECRET)
        assert first.startswith('fernet:v1:') and SECRET not in first
        assert first != second
        assert cipher.decrypt(first) == SECRET
        assert FernetFileCipher(tmp_path).decrypt(second) == SECRET
        assert stat.S_IMODE((tmp_path / '.secrets').stat().st_mode) == 0o700
        assert stat.S_IMODE(key_path(tmp_path).stat().st_mode) == 0o600
        assert key_path(tmp_path).stat().st_uid == os.getuid()
        assert sorted(path.name for path in (tmp_path / '.secrets').iterdir()) == ['model.key']

    def test_independent_process_reuses_persisted_key(self, tmp_path):
        encrypted = FernetFileCipher(tmp_path).encrypt(SECRET)
        code = ('import json,sys; from pathlib import Path; '
                'from server.credential_cipher import FernetFileCipher; '
                'p=json.load(sys.stdin); '
                'assert FernetFileCipher(Path(p["root"])).decrypt(p["ciphertext"]) == p["expected"]')
        result = subprocess.run([sys.executable, '-c', code],
                                input=json.dumps({'root': str(tmp_path), 'ciphertext': encrypted,
                                                  'expected': SECRET}),
                                text=True, capture_output=True, timeout=30,
                                cwd=Path(__file__).resolve().parents[2])
        assert result.returncode == 0, 'Independent process could not decrypt its test credential'
        assert not result.stdout and not result.stderr

    def test_concurrent_first_use_publishes_one_complete_key(self, tmp_path):
        barrier = threading.Barrier(8)

        def encrypt(index):
            cipher = FernetFileCipher(tmp_path)
            barrier.wait(timeout=15)
            return cipher.encrypt(f'{SECRET}-{index}')

        with ThreadPoolExecutor(max_workers=8) as executor:
            encrypted = list(executor.map(encrypt, range(8)))
        restarted = FernetFileCipher(tmp_path)
        assert [restarted.decrypt(value) for value in encrypted] == [f'{SECRET}-{i}' for i in range(8)]
        assert sorted(path.name for path in (tmp_path / '.secrets').iterdir()) == ['model.key']

    def test_decryption_never_creates_missing_key(self, tmp_path):
        with pytest.raises(CredentialError) as caught:
            FernetFileCipher(tmp_path).decrypt('fernet:v1:synthetic-missing-token')
        assert not key_path(tmp_path).exists()
        assert 'synthetic-missing-token' not in str(caught.value)

    def test_existing_ciphertext_prevents_generating_a_replacement_key(self, tmp_path):
        with pytest.raises(CredentialError):
            FernetFileCipher(tmp_path, has_ciphertext=lambda: True).encrypt(SECRET)
        assert not key_path(tmp_path).exists()

    def test_corrupt_key_is_rejected_without_overwriting_it(self, tmp_path):
        cipher = FernetFileCipher(tmp_path)
        encrypted = cipher.encrypt(SECRET)
        damaged = b'synthetic-damaged-master-key'
        key_path(tmp_path).write_bytes(damaged)
        for action in (lambda: cipher.encrypt(SECRET), lambda: cipher.decrypt(encrypted)):
            with pytest.raises(CredentialError) as caught:
                action()
            assert SECRET not in str(caught.value)
            assert damaged.decode() not in str(caught.value)
        assert key_path(tmp_path).read_bytes() == damaged

    @pytest.mark.parametrize('target,mode', [('directory', 0o755), ('key', 0o644)])
    def test_permissions_are_checked_again_on_each_operation(self, tmp_path, target, mode):
        cipher = FernetFileCipher(tmp_path)
        encrypted = cipher.encrypt(SECRET)
        original = key_path(tmp_path).read_bytes()
        path = tmp_path / '.secrets' if target == 'directory' else key_path(tmp_path)
        path.chmod(mode)
        with pytest.raises(CredentialError):
            cipher.encrypt(SECRET)
        with pytest.raises(CredentialError):
            cipher.decrypt(encrypted)
        assert key_path(tmp_path).read_bytes() == original
        assert stat.S_IMODE(path.stat().st_mode) == mode

    @pytest.mark.parametrize('target', ['directory', 'key'])
    def test_symlink_is_rejected_without_writing_through_it(self, tmp_path, target):
        data = tmp_path / 'data'
        data.mkdir()
        outside = tmp_path / 'outside'
        outside.mkdir(mode=0o700)
        if target == 'directory':
            (data / '.secrets').symlink_to(outside, target_is_directory=True)
            untouched = outside / 'model.key'
        else:
            (data / '.secrets').mkdir(mode=0o700)
            untouched = outside / 'original.key'
            untouched.write_bytes(b'synthetic-test-target-not-a-key')
            untouched.chmod(0o600)
            key_path(data).symlink_to(untouched)
        before = untouched.read_bytes() if untouched.exists() else None
        with pytest.raises(CredentialError):
            FernetFileCipher(data).encrypt(SECRET)
        with pytest.raises(CredentialError):
            FernetFileCipher(data).decrypt('fernet:v1:synthetic-token')
        assert (untouched.read_bytes() if untouched.exists() else None) == before

    def test_directory_in_place_of_key_is_rejected(self, tmp_path):
        (tmp_path / '.secrets').mkdir(mode=0o700)
        key_path(tmp_path).mkdir(mode=0o700)
        with pytest.raises(CredentialError):
            FernetFileCipher(tmp_path).encrypt(SECRET)
        assert key_path(tmp_path).is_dir()

    def test_wrong_service_owner_is_rejected_without_changing_key(self, tmp_path, monkeypatch):
        cipher = FernetFileCipher(tmp_path)
        encrypted = cipher.encrypt(SECRET)
        before = key_path(tmp_path).read_bytes()
        actual_uid = os.geteuid()
        monkeypatch.setattr(os, 'geteuid', lambda: actual_uid + 1)
        with pytest.raises(CredentialError):
            cipher.encrypt(SECRET)
        with pytest.raises(CredentialError):
            cipher.decrypt(encrypted)
        assert key_path(tmp_path).read_bytes() == before

    def test_missing_crypto_dependency_cannot_save_plaintext(self, tmp_path, monkeypatch):
        original_import = builtins.__import__

        def unavailable(name, *args, **kwargs):
            if name == 'cryptography.fernet':
                raise ImportError('synthetic-dependency-failure')
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, '__import__', unavailable)
        with pytest.raises(CredentialError) as caught:
            FernetFileCipher(tmp_path).encrypt(SECRET)
        assert SECRET not in str(caught.value)
        assert 'synthetic-dependency-failure' not in str(caught.value)
        assert not key_path(tmp_path).exists()

    def test_failed_publication_leaves_no_partial_key_or_temporary_file(self, tmp_path, monkeypatch):
        def cannot_link(*args, **kwargs):
            raise OSError('synthetic-publish-failure')

        monkeypatch.setattr(os, 'link', cannot_link)
        with pytest.raises(CredentialError) as caught:
            FernetFileCipher(tmp_path).encrypt(SECRET)
        assert SECRET not in str(caught.value)
        assert 'synthetic-publish-failure' not in str(caught.value)
        assert not key_path(tmp_path).exists()
        assert list((tmp_path / '.secrets').iterdir()) == []

    def test_wrong_master_key_and_modified_token_fail_authentication(self, tmp_path):
        left = tmp_path / 'left'
        right = tmp_path / 'right'
        left.mkdir()
        right.mkdir()
        first = FernetFileCipher(left)
        second = FernetFileCipher(right)
        encrypted = first.encrypt(SECRET)
        second.encrypt('another-synthetic-test-credential')
        with pytest.raises(CredentialError):
            second.decrypt(encrypted)
        index = len(encrypted) // 2
        changed = encrypted[:index] + ('A' if encrypted[index] != 'A' else 'B') + encrypted[index + 1:]
        with pytest.raises(CredentialError):
            first.decrypt(changed)

    @pytest.mark.parametrize('value', [SECRET, 'dpapi:v1:synthetic-token', 'fernet:v2:synthetic-token'])
    def test_unknown_or_other_platform_ciphertext_has_no_plaintext_fallback(self, tmp_path, value):
        with pytest.raises(CredentialError) as caught:
            FernetFileCipher(tmp_path).decrypt(value)
        assert SECRET not in str(caught.value)
        assert 'synthetic-token' not in str(caught.value)
        assert not key_path(tmp_path).exists()

    def test_service_persists_encrypted_revisions_and_restarts(self, tmp_path):
        service = ModelService(Store(tmp_path / 'data'))
        saved = service.save_config(model_payload(apiKey=SECRET))
        service.save_preferences({'defaultModelId': saved['id']})
        assert saved['hasApiKey'] is True
        assert_redacted(json.dumps(saved))
        with service.store.connect() as db:
            row = db.execute('SELECT config_json,key_cipher FROM ai_model_configs').fetchone()
        assert row['key_cipher'].startswith('fernet:v1:')
        assert SECRET not in row['key_cipher'] and SECRET not in row['config_json']
        preserved = service.save_config({'name': '更新配置', 'apiKey': ''}, saved['id'])
        assert preserved['version'] == 2 and preserved['hasApiKey']
        restarted = ModelService(Store(service.store.root))
        assert restarted.resolve()['apiKey'] == SECRET
        assert restarted.resolve(saved['id'], 1)['apiKey'] == SECRET
        cleared = restarted.save_config({'clearApiKey': True}, saved['id'])
        assert cleared['hasApiKey'] is False
        assert restarted.resolve()['apiKey'] == ''
        assert restarted.resolve(saved['id'], 1)['apiKey'] == SECRET

    @pytest.mark.parametrize('latest_state', ['active', 'cleared', 'deleted'])
    def test_missing_key_preserves_old_versions_and_blocks_replacement(self, tmp_path, latest_state):
        service = ModelService(Store(tmp_path / 'data'))
        saved = service.save_config(model_payload(apiKey=SECRET))
        if latest_state == 'cleared':
            service.save_config({'clearApiKey': True}, saved['id'])
        elif latest_state == 'deleted':
            service.delete_config(saved['id'])
        with service.store.connect() as db:
            before = db.execute('SELECT COUNT(*) FROM ai_model_configs').fetchone()[0]
        key_path(service.store.root).unlink()
        restarted = ModelService(Store(service.store.root))
        with pytest.raises(ModelError) as caught:
            restarted.save_config(model_payload(apiKey='replacement-synthetic-credential'))
        assert 'replacement-synthetic-credential' not in str(caught.value)
        assert not key_path(service.store.root).exists()
        with service.store.connect() as db:
            assert db.execute('SELECT COUNT(*) FROM ai_model_configs').fetchone()[0] == before
        with pytest.raises(ModelError):
            restarted.resolve(saved['id'], 1)
        assert restarted.save_config(model_payload())['hasApiKey'] is False

    def test_api_responses_and_failure_do_not_expose_credentials(self, tmp_path):
        from fastapi.testclient import TestClient
        from server.app import create_app

        app = create_app(data_dir=tmp_path / 'data')
        with TestClient(app) as client:
            created = client.post('/api/models', json=model_payload(apiKey=SECRET), headers=LOCAL)
            assert created.status_code == 201
            assert_redacted(created.text)
            saved = created.json()['model']
            listed = client.get('/api/models')
            assert listed.status_code == 200
            assert_redacted(listed.text)
            key_path(app.state.store.root).chmod(0o644)
            failed = client.put(f'/api/models/{saved["id"]}',
                                json={'apiKey': 'replacement-synthetic-credential'}, headers=LOCAL)
            assert failed.status_code == 422
            assert_redacted(failed.text, 'replacement-synthetic-credential')
            assert 'Windows' not in failed.json()['detail']
            listed_again = client.get('/api/models')
            assert listed_again.json()['models'][0]['version'] == 1
            assert_redacted(listed_again.text)
