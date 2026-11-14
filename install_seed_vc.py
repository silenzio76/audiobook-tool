"""Explicit isolated Seed-VC installation; --check never downloads anything."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from project_storage import save, atomic

HERE=Path(__file__).resolve().parent
SOURCE_REVISION='51383efd921027683c89e5348211d93ff12ac2a8'
MODELS={
    'Plachta/Seed-VC': ('257283f9f41585055e8f858fba4fd044e5caed6e',[
        'DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth','config_dit_mel_seed_uvit_whisper_small_wavenet.yml']),
    'funasr/campplus': ('e4b6ede7ce16997aff4ae69fbca1f0175e2afede',['campplus_cn_common.bin']),
    'openai/whisper-small': ('973afd24965f72e36ca33b3055d56a652f456b4d',[
        'config.json','preprocessor_config.json','model.safetensors']),
    'nvidia/bigvgan_v2_22khz_80band_256x': ('633ff708ed5b74903e86ff1298cf4a98e921c513',[
        'config.json','bigvgan_generator.pt']),
}


def sha(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''): result.update(block)
    return result.hexdigest()


def probe(home, root=HERE):
    """Read-only GPU/package/model validation in the isolated interpreter."""
    python=home/'venv/Scripts/python.exe'
    code=(
        'import json,torch,torchaudio,importlib.metadata as m; '
        'assert torch.cuda.is_available(), "CUDA unavailable"; '
        'x=torch.ones((32,32),device="cuda"); assert (x@x).mean().item()==32; '
        'print(json.dumps({"gpu":torch.cuda.get_device_name(0),"torch":torch.__version__, '
        '"torchaudio":torchaudio.__version__,"packages":{d.metadata["Name"].lower():d.version for d in m.distributions()}}))'
    )
    result=subprocess.run([str(python),'-X','utf8','-c',code],check=True,capture_output=True,text=True)
    info=json.loads(result.stdout)
    for name in ('requirements-seed-vc.txt','requirements-seed-vc-torch.txt'):
        for line in (root/name).read_text().splitlines():
            if not line or line.startswith('#'): continue
            package,version=line.split('==')
            if info['packages'].get(package.lower())!=version:
                raise ValueError('Seed-VC dependency differs from lock: '+package)
    revision=subprocess.run(['git','-C',str(home/'source'),'rev-parse','HEAD'],check=True,
                            capture_output=True,text=True).stdout.strip()
    if revision!=SOURCE_REVISION: raise ValueError('Seed-VC source revision differs from verified version.')
    manifest=json.loads((home/'models-manifest.json').read_text())
    if set(manifest)!=set(MODELS): raise ValueError('Incomplete model manifest.')
    for repo,record in manifest.items():
        if record['revision']!=MODELS[repo][0]: raise ValueError('Unexpected model revision: '+repo)
        if {e['name'] for e in record['files']}!=set(MODELS[repo][1]):
            raise ValueError('Incomplete model files: '+repo)
        for entry in record['files']:
            if sha(entry['path'])!=entry['sha256']: raise ValueError('Model hash mismatch: '+entry['name'])
    return info


def download(home):
    """Download only pinned official model files after explicit user invocation."""
    from huggingface_hub import hf_hub_download
    records={}
    for repo,(revision,names) in MODELS.items():
        cache=home/'source/checkpoints' if repo in ('Plachta/Seed-VC','funasr/campplus') else home/'source/checkpoints/hf_cache'
        entries=[]
        for name in names:
            path=Path(hf_hub_download(repo,filename=name,revision=revision,cache_dir=cache))
            entries.append({'name':name,'path':str(path),'size':path.stat().st_size,'sha256':sha(path)})
        records[repo]={'revision':revision,'files':entries}
        atomic(cache/('models--'+repo.replace('/','--'))/'refs/main',revision.encode())
    save(home/'models-manifest.json',records)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check',action='store_true')
    parser.add_argument('--download-models',action='store_true')
    parser.add_argument('--fetch-models-only',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    home=HERE/'.runtime/seed-vc'
    if args.check:
        info=probe(home)
        print(json.dumps({k:v for k,v in info.items() if k!='packages'},indent=2))
        return
    if args.fetch_models_only:
        download(home)
        return
    if sys.version_info[:2]!=(3,10) or sys.platform!='win32':
        parser.error('Use 64-bit Python 3.10 on Windows, for example: py -3.10 install_seed_vc.py --download-models')
    home.mkdir(parents=True,exist_ok=True)
    if home.is_symlink() or (home/'venv').is_symlink() or (home/'source').is_symlink():
        parser.error('Refusing a linked installation directory.')
    source=home/'source'
    if not source.exists():
        subprocess.run(['git','clone','https://github.com/Plachtaa/seed-vc.git',str(source)],check=True)
        subprocess.run(['git','-C',str(source),'checkout',SOURCE_REVISION],check=True)
    revision=subprocess.run(['git','-C',str(source),'rev-parse','HEAD'],check=True,capture_output=True,text=True).stdout.strip()
    if revision!=SOURCE_REVISION: parser.error('Preserve the existing source checkout and reconcile its revision first.')
    python=home/'venv/Scripts/python.exe'
    if not python.exists(): subprocess.run([sys.executable,'-m','venv',str(home/'venv')],check=True)
    subprocess.run([str(python),'-m','ensurepip'],check=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(HERE/'requirements-seed-vc-torch.txt'),
                    '--index-url','https://download.pytorch.org/whl/cu121'],check=True)
    subprocess.run([str(python),'-m','pip','install','-r',str(HERE/'requirements-seed-vc.txt')],check=True)
    subprocess.run([str(python),'-m','pip','check'],check=True)
    if args.download_models:
        env=dict(os.environ,HF_HOME=str(home/'huggingface'),HF_HUB_CACHE=str(source/'checkpoints/hf_cache'),
                 HF_HUB_DISABLE_TELEMETRY='1',HF_HUB_DISABLE_XET='1')
        env.pop('HF_HUB_OFFLINE',None)
        subprocess.run([str(python),str(HERE/'install_seed_vc.py'),'--fetch-models-only'],env=env,check=True)
    if not (home/'models-manifest.json').exists():
        print('Runtime installed. Models require a separate explicit --download-models invocation.')
        return
    info=probe(home)
    save(home/'installation.json',dict(info,source_revision=SOURCE_REVISION,interpreter=str(python)))
    atomic(home/'requirements-resolved.txt',''.join(k+'=='+v+'\n' for k,v in sorted(info['packages'].items())).encode())
    save(home/'ready.json',{'installation_ready':True,'source_revision':SOURCE_REVISION,
                           'gpu':info['gpu'],'phonetic_acceptance':False,'listening_acceptance':False})
    print('Seed-VC installed and GPU/model hashes verified; listening acceptance remains open.')


if __name__=='__main__': main()
