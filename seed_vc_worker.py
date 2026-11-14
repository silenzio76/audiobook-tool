"""Persistent isolated Seed-VC worker; protocol goes to stdout, upstream logs to stderr."""
from pathlib import Path
import contextlib
import hashlib
import json
import os
import sys
import tempfile
from types import SimpleNamespace


def send(message):
    print(json.dumps(message),flush=True)


def file_hash(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            digest.update(block)
    return digest.hexdigest()


def main():
    home=Path(sys.argv[1]).resolve()
    os.chdir(home/'source')
    sys.path.insert(0,str(home/'source'))
    for key in ('HF_HUB_OFFLINE','TRANSFORMERS_OFFLINE','HF_HUB_DISABLE_TELEMETRY'):
        os.environ[key]='1'
    manifest=json.loads((home/'models-manifest.json').read_text())
    loaded=None
    inference=None
    for line in sys.stdin:
        try:
            job=json.loads(line)
            if job.get('quit'): break
            source,reference,output=(Path(job[k]) for k in ('source','reference','output'))
            settings=job['settings']
            args=SimpleNamespace(source=str(source),target=str(reference),output='',
                                 diffusion_steps=settings['diffusion_steps'],length_adjust=settings['length_adjust'],
                                 inference_cfg_rate=settings['inference_cfg_rate'],fp16=settings['fp16'],
                                 f0_condition=False,auto_f0_adjust=False,semi_tone_shift=0,
                                 checkpoint=None,config=None)
            with contextlib.redirect_stdout(sys.stderr):
                import numpy as np
                import soundfile as sf
                if loaded is None:
                    send_stream=sys.__stdout__
                    send_stream.write('{"kind":"loading"}\n'); send_stream.flush()
                    for repo,record in manifest.items():
                        cache=home/'source/checkpoints' if repo in ('Plachta/Seed-VC','funasr/campplus') else home/'source/checkpoints/hf_cache'
                        ref=cache/('models--'+repo.replace('/','--'))/'refs/main'
                        if ref.read_text().strip()!=record['revision']:
                            raise ValueError('Revisione del modello locale cambiata: '+repo)
                        for entry in record['files']:
                            if file_hash(entry['path'])!=entry['sha256']:
                                raise ValueError('File del modello alterato: '+entry['name'])
                    import torch
                    if not torch.cuda.is_available():
                        raise RuntimeError('Seed-VC richiede la GPU CUDA verificata.')
                    import inference as upstream
                    inference=upstream
                    primary=manifest['Plachta/Seed-VC']['files']
                    args.checkpoint=next(e['path'] for e in primary if e['name'].endswith('.pth'))
                    args.config=next(e['path'] for e in primary if e['name'].endswith('.yml'))
                    loaded=inference.load_models(args)
                    inference.load_models=lambda _:loaded
                import torch
                torch.manual_seed(1729)
                np.random.seed(1729)
                with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
                    args.output=temporary
                    inference.main(args)
                    candidates=list(Path(temporary).glob('*.wav'))
                    if len(candidates)!=1: raise ValueError('Risultato Seed-VC mancante.')
                    samples,rate=sf.read(candidates[0],dtype='float32')
                    original,original_rate=sf.read(source)
                    if samples.ndim!=1 or not samples.size or rate!=22050 or not np.isfinite(samples).all():
                        raise ValueError('Audio Seed-VC non valido.')
                    peak=float(np.max(abs(samples)))
                    if peak<.00001: raise ValueError('Audio Seed-VC silenzioso.')
                    if abs(len(samples)/rate-len(original)/original_rate)>max(.5,len(original)/original_rate*.05):
                        raise ValueError('Durata Seed-VC inattesa.')
                    candidate=Path(temporary)/'validated.wav'
                    sf.write(candidate,samples*min(1,.98/peak),rate,subtype='PCM_16')
                    os.replace(candidate,output)
            send({'kind':'complete','output':str(output),'frames':len(samples),'sample_rate':rate})
        except Exception as error:
            send({'kind':'error','message':str(error)})


if __name__=='__main__':
    main()
