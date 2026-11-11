"""Build a reproducible, allowlisted source candidate for repository review."""
import argparse
import ast
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile


HERE = Path(__file__).resolve().parent
# No recursive discovery: adding a file requires a deliberate allowlist edit.
EXPORT_FILES = (
    '.gitignore', '.gitattributes', 'README.md', 'ROADMAP.md', 'LICENSE', 'VERSION', 'CHANGELOG.md',
    'source_export.py', 'version_info.py', 'BUILD', 'AGENTS.md',
    'dev_commit.py', 'test_dev_commit.py',
    'audiolibro.py', 'analisi_audio.py', 'converti_audio.py',
    'editor_audiolibro.py', 'equalizzatore_voci.py', 'gestione_voci.py',
    'installa_qwen.py', 'lingue_audio.py', 'montaggio_audio.py',
    'numeri_italiani.py', 'pannelli_voce.py', 'pipeline_audio.py',
    'profili_vocali.py', 'qwen_backend.py', 'sintesi_lingue.py',
    'ui_language.py', 'voce_windows.ps1', 'requirements-editor.txt',
    'test_source_export.py', 'test_standalone.py',
)
MANIFEST = 'SOURCE_MANIFEST.json'
BUILD_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,79}', re.ASCII)
EXCLUSIONS = [
    'manuscripts and chapter plans', 'local voice profiles and pronunciation lexicon',
    'voice samples and model weights', 'runtime environments and caches',
    'generated audio, analysis reports, backups and temporary files',
    'book-specific automation and existing editorial test fixtures', 'Git history',
]


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def collect(source=HERE):
    """Read approved files without following links or including unlisted data."""
    source = Path(source).resolve()
    payloads = {}
    for name in EXPORT_FILES:
        path = source / name
        if path.is_symlink() or path.resolve().parent != source:
            raise ValueError('Linked or external export input: ' + name)
        if not path.is_file():
            raise ValueError('Missing approved export input: ' + name)
        payloads[name] = path.read_bytes()
    # Detect a newly introduced dependency on a local module not being exported.
    modules = {Path(name).stem for name in EXPORT_FILES if name.endswith('.py')}
    local_modules = {path.stem for path in source.glob('*.py')}
    for name, payload in payloads.items():
        if not name.endswith('.py'):
            continue
        tree = ast.parse(payload.decode('utf-8-sig'), filename=name)
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.Import):
                imports = [item.name.split('.')[0] for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports = [node.module.split('.')[0]]
            for module in imports:
                if module in local_modules and module not in modules:
                    raise ValueError(f'Unexported local dependency: {name} -> {module}')
    # A source changing while it is read cannot be described as one verified snapshot.
    for name, payload in payloads.items():
        if (source / name).read_bytes() != payload:
            raise ValueError('Source changed during export: ' + name)
    return payloads


def make_archive(payloads, build_id, *, source_commit=None, release=False):
    """Use fixed metadata and stored bytes for byte-identical repeated builds."""
    if not isinstance(build_id, str) or not BUILD_ID.fullmatch(build_id):
        raise ValueError('Build ID must be 1-80 ASCII letters, digits, dots, underscores or hyphens.')
    if set(payloads) != set(EXPORT_FILES):
        raise ValueError('Export payload does not match the approved allowlist.')
    version = payloads['VERSION'].decode('ascii').strip()
    if not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', version):
        raise ValueError('Invalid authoritative VERSION.')
    if release and not re.fullmatch(r'[0-9a-f]{40,64}', source_commit or ''):
        raise ValueError('Release builds require verified Git commit provenance.')
    build_text = payloads['BUILD'].decode('ascii').strip()
    if not re.fullmatch(r'[1-9][0-9]*', build_text):
        raise ValueError('Invalid authoritative BUILD.')
    files = {name: {'sha256': digest(payload), 'bytes': len(payload)}
             for name, payload in sorted(payloads.items())}
    manifest = {
        'schema_version': 1,
        'kind': 'source-release' if release else 'source-candidate',
        'release': release,
        'release_version': version,
        'build_number': int(build_text),
        'build_version': version + '+build.' + build_text,
        'license': 'GPL-3.0-or-later',
        'repository': 'https://github.com/silenzio76/audiobook-tool',
        'source_commit': source_commit,
        'source_tag': 'v' + version if release else None,
        'build_id': build_id,
        'source_snapshot_sha256': digest(json_bytes(files)),
        'files': files,
        'exclusions': EXCLUSIONS,
        'pending_decisions': [],
        'review_limits': [
            'The allowlist excludes local data; it is not a credential or copyright audit.',
            'Source presets retain series character names; localized strings remain Italian.',
            'No clean-machine installation, GUI or synthesis is certified.',
        ],
    }
    entries = dict(payloads, **{MANIFEST: json_bytes(manifest)})
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as archive:
        for name, payload in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, payload)
    return output.getvalue(), manifest


def verify_archive(payload, expected_payloads, expected_manifest):
    """Reject missing, extra, duplicated or altered members before publication."""
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        names = archive.namelist()
        expected = set(expected_payloads) | {MANIFEST}
        if len(names) != len(expected) or set(names) != expected or archive.testzip():
            raise ValueError('Archive members or CRC validation failed.')
        if archive.read(MANIFEST) != json_bytes(expected_manifest):
            raise ValueError('Archive manifest validation failed.')
        for name, data in expected_payloads.items():
            if archive.read(name) != data:
                raise ValueError('Archive content validation failed: ' + name)


def release_commit(source, payloads):
    """Bind a release to exactly the approved, committed files and version tag."""
    source = Path(source).resolve()
    if not (source / '.git').exists():
        raise ValueError('Release requires a dedicated Git checkout.')

    def git(*args):
        result = subprocess.run(['git', '-C', str(source), *args], capture_output=True,
                                text=True, encoding='utf-8', check=False)
        if result.returncode:
            raise ValueError('Git release check failed: ' + result.stderr.strip())
        return result.stdout.strip()

    if git('status', '--porcelain', '--untracked-files=normal'):
        raise ValueError('Release checkout must be clean.')
    tracked = set(git('ls-files', '-z').rstrip('\0').split('\0'))
    if tracked != set(EXPORT_FILES):
        raise ValueError('Tracked files differ from the approved release allowlist.')
    commit = git('rev-parse', 'HEAD')
    version = payloads['VERSION'].decode('ascii').strip()
    if git('rev-parse', 'refs/tags/v' + version + '^{commit}') != commit:
        raise ValueError('Version tag must point to the release commit.')
    for name, payload in payloads.items():
        committed = subprocess.run(['git', '-C', str(source), 'show', commit + ':' + name],
                                   capture_output=True, check=False)
        if committed.returncode or committed.stdout != payload:
            raise ValueError('Source bytes differ from the release commit: ' + name)
    return commit


def export_candidate(destination, build_id, source=HERE, *, release=False):
    """Validate a complete staged archive, then atomically publish a new artifact."""
    payloads = collect(source)
    commit = release_commit(source, payloads) if release else None
    payload, manifest = make_archive(payloads, build_id, source_commit=commit, release=release)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    checksum = digest(payload)
    prefix = 'audiobook-tool-' + manifest['release_version'] if release else 'audiobook-source'
    output = destination / (prefix + '-' + checksum + '.zip')
    # Repeated builds are idempotent. Never overwrite a damaged existing artifact.
    if output.exists() or output.is_symlink():
        if output.is_symlink() or output.read_bytes() != payload:
            raise ValueError('Existing candidate differs from its checksum: ' + str(output))
        verify_archive(output.read_bytes(), payloads, manifest)
    else:
        with tempfile.NamedTemporaryFile(dir=destination, suffix='.tmp', delete=False) as handle:
            staged = Path(handle.name)
        try:
            staged.write_bytes(payload)
            if digest(staged.read_bytes()) != checksum:
                raise ValueError('Staged archive checksum validation failed.')
            verify_archive(staged.read_bytes(), payloads, manifest)
            os.replace(staged, output)
        finally:
            staged.unlink(missing_ok=True)
    return {'artifact': str(output), 'sha256': checksum, 'bytes': len(payload),
            'build_id': build_id, 'source_snapshot_sha256': manifest['source_snapshot_sha256'],
            'files': len(payloads), 'release': release,
            'version': manifest['release_version'], 'source_commit': commit}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path, help='Directory for the verified source ZIP')
    parser.add_argument('--build-id', required=True, help='Explicit identifier for this candidate build')
    parser.add_argument('--release', action='store_true', help='Require clean Git provenance and a matching version tag')
    args = parser.parse_args()
    try:
        result = export_candidate(args.destination, args.build_id, release=args.release)
    except (OSError, ValueError, SyntaxError, zipfile.BadZipFile) as error:
        parser.exit(1, f'Source export failed: {error}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
