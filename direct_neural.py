"""Validated operational text and original recipes for direct neural listening tests."""
import hashlib
import json
from pathlib import Path

VERSION='direct-qwen-1'
MODEL='Qwen/Qwen3-TTS-12Hz-0.6B-Base'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def operational_input(plan,segment):
    """Use a hash-bound orthographic counterpart; never guess prose from IPA."""
    from lingue_audio import segment_language,QWEN,speech_parts
    if segment_language(plan,segment)!='ipa-it':
        parts=speech_parts(segment.get('spoken_text',segment['text']),segment_language(plan,segment))
        if any(p.get('audio') or p['language'] not in QWEN for p in parts):
            raise ValueError('Qwen diretto richiede una lingua supportata e testo operativo verificabile.')
        return parts,None
    source=Path(plan['source'])
    report_path=source.with_name(source.name[:-7]+'.fonetica.json') if source.name.lower().endswith('.ipa.md') else None
    if report_path is None or not report_path.is_file():
        raise ValueError('Qwen diretto: manca il rapporto .fonetica.json con il testo abbinato all’IPA.')
    report=json.loads(report_path.read_text(encoding='utf-8'))
    if (not isinstance(report,dict) or not isinstance(report.get('samples'),list)
            or any(not isinstance(s,dict) for s in report['samples'])):
        raise ValueError('Qwen diretto: rapporto fonetico non valido.')
    if digest(source)!=plan['sha256'] or report.get('ipa_sha256')!=plan['sha256']:
        raise ValueError('Qwen diretto: il rapporto non corrisponde alla versione del file IPA.')
    matches=[s for s in report.get('samples',[]) if s.get('index')==segment['id']]
    if len(matches)!=1:
        raise ValueError('Qwen diretto: segmento senza corrispondenza ortografica univoca.')
    item=matches[0]
    if item.get('ipa')!=segment.get('spoken_text',segment['text']):
        raise ValueError('Qwen diretto: IPA modificato; aggiornare prima il testo abbinato.')
    text=item.get('spoken_text')
    if (not isinstance(text,str) or not text.strip()
            or hashlib.sha256(text.encode()).hexdigest()!=item.get('spoken_sha256')):
        raise ValueError('Qwen diretto: testo ortografico alterato o mancante.')
    if not isinstance(item.get('source'),str) or not isinstance(item.get('source_sha256'),str):
        raise ValueError('Qwen diretto: provenienza del campione mancante.')
    if digest(item['source'])!=item['source_sha256']:
        raise ValueError('Qwen diretto: la fonte del campione è cambiata.')
    return [{'text':text,'language':'it'}],digest(report_path)


def recipe(reference):
    """Resolve the original conditioning, not the newly imported synthetic WAV."""
    from qwen_backend import reference as verified_reference
    metadata,_=verified_reference(reference)
    value=metadata.get('original_generation_recipe')
    if not isinstance(value,dict) or value.get('model')!=MODEL:
        raise ValueError('Il profilo selezionato non contiene un preset originale Qwen diretto.')
    if any(not isinstance(value.get(k),str) or not value[k].strip()
           for k in ('revision','voice_reference','voice_reference_sha256')):
        raise ValueError('Preset Qwen diretto incompleto: riferimento o revisione mancanti.')
    settings=value.get('settings')
    expected={'do_sample':True,'temperature':.8,'top_p':.95,'top_k':50,
              'repetition_penalty':1.05,'max_new_tokens':1024,'non_streaming_mode':True}
    if settings!=expected or value.get('seed')!=1729 or type(value.get('x_vector_only_mode')) is not bool:
        raise ValueError('Preset Qwen diretto modificato: verificare impostazioni e seme.')
    original=value['voice_reference']
    _,wav=verified_reference(original)
    if digest(wav)!=value['voice_reference_sha256']:
        raise ValueError('Il riferimento originale del preset è cambiato.')
    transcript=''; transcript_hash=None
    if not value['x_vector_only_mode']:
        if not isinstance(value.get('reference_transcript_file'),str) or not value['reference_transcript_file']:
            raise ValueError('Preset B privo della trascrizione di riferimento.')
        path=Path(value['reference_transcript_file'])
        info=json.loads(path.read_text(encoding='utf-8'))
        if info.get('reference_sha256')!=digest(wav) or not info.get('transcript','').strip():
            raise ValueError('Trascrizione di riferimento non corrispondente al preset B.')
        transcript=info['transcript']; transcript_hash=digest(path)
    return {'version':VERSION,'model':MODEL,'revision':value['revision'],
            'reference':str(Path(original).resolve()),'reference_audio_sha256':digest(wav),
            'reference_metadata_sha256':digest(original),'transcript':transcript,
            'transcript_sha256':transcript_hash,'embedding_only':value['x_vector_only_mode'],
            'seed':1729,'settings':dict(expected)}
