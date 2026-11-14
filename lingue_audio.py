"""Conservative script and explicit-lexicon routing without translation."""
import json
from pathlib import Path
import re
from numeri_italiani import spoken_it

VERSION='multilingue-5-ipa-extended'
LEXICON=Path(__file__).with_name('pronunce.json')
LANGUAGES={'auto':'Automatico', 'it':'Italiano', 'el':'Greco moderno', 'ar':'Arabo',
           'en':'Inglese','fr':'Francese','de':'Tedesco','es':'Spagnolo','pt':'Portoghese','ru':'Russo','la':'Latino',
           'he':'Ebraico (WAV se voce non disponibile)','hy':'Armeno',
           'zh':'Cinese','ja':'Giapponese','ko':'Coreano'}
QWEN={'it':'Italian','en':'English','fr':'French','de':'German','es':'Spanish','pt':'Portuguese','ru':'Russian','zh':'Chinese','ja':'Japanese','ko':'Korean'}
SCRIPT=re.compile(r'[\u0370-\u03ff\u1f00-\u1fff]+(?:[\s,;·]+[\u0370-\u03ff\u1f00-\u1fff]+)*|[\u0600-\u06ff]+(?:[\s,;]+[\u0600-\u06ff]+)*')


# Retain the stored code for compatibility with existing plans and overrides.
LANGUAGES['ipa-it'] = 'IPA — Multilingue (voce eSpeak NG)'
DOCUMENT_LANGUAGES = ('auto', 'it', 'en', 'ipa-it')


def segment_language(plan, segment):
    selected = segment.get('language', 'auto')
    return plan.get('language', 'auto') if selected == 'auto' else selected


# Local cues: a left cue must end before the homograph;
# a right cue must begin immediately after it. Punctuation and language
# switches terminate the context.
OMOGRAPHS = {
    'pesca': (
        ('pèsca', r"(?:mangiò|mangia|sbucciò|sbuccia|addentò|addenta|raccolse) (?:una|la)", r"matura|succosa|acerba"),
        ('pésca', r"(?:andare|andò|andiamo|uscì|uscire|barca|canna|rete) (?:a|da|di)", r"del tonno|in mare|sportiva|d’altura|d'altura"),
    ),
    'ancora': (
        ('àncora', r"(?:gettò|getta|gettarono|calò|cala|levò|leva|issò|salpò|salpa|mollò) (?:l['’]|un['’]|la)", r"di ferro|della nave"),
        ('ancòra', r"(?:non (?:è|era|sono|aveva|hanno|ha)|una volta)", r"oggi|domani|adesso|una volta|di nuovo"),
    ),
    'ambito': (
        ('àmbito', r"(?:nell['’]|dall['’]|all['’]|in questo|nel proprio)", r"di ricerca|d['’]azione|lavorativo|professionale"),
        ('ambìto', r"(?:premio|incarico|posto|riconoscimento|titolo|traguardo)(?: molto)?", r"da tutti|da molti"),
    ),
    'principi': (
        ('prìncipi', r"(?:re|duchi|conti) e(?: i)?", r"ereditari|regnanti|di sangue"),
        ('princìpi', r"(?:rispettare|violare|applicare|difendere) (?:i|questi)", r"fondamentali|morali|etici|costituzionali|di diritto"),
    ),
    'leggere': (
        ('lèggere', r"(?:sa|sapeva|vuole|voleva|può|poteva|deve|doveva|iniziò a|cominciò a|imparò a|mi piace)", r"(?:il|un) libro|(?:una|la) lettera|ad alta voce|in silenzio"),
        ('leggère', r"(?:armi|vesti|stoffe|coperte|armature|piume|calzature|cose)(?: molto)?", r"come piume|come una piuma"),
    ),
    'venti': (
        ('vénti', r"(?:alle|meno di|più di|circa|oltre|almeno)", r"anni|minuti|giorni|ore|mesi|passi|soldati|persone|monete|metri"),
        ('vènti', r"(?:raffiche|folate) di", r"forti|contrari|favorevoli|freddi|caldi|impetuosi|di tramontana"),
    ),
}

OMOGRAPH_WORD = re.compile(r'\b(?:' + '|'.join(OMOGRAPHS) + r')\b', re.IGNORECASE)
ITALIAN_STRESS = re.compile(r'\banzian[oaie]\b', re.IGNORECASE)
CONTEXT_RULES = {
    word: [(accent, re.compile(r'(?:^|[^\w])(?:' + before + r')\s*$', re.IGNORECASE),
            re.compile(r'^\s+(?:' + after + r')\b', re.IGNORECASE))
           for accent, before, after in readings]
    for word, readings in OMOGRAPHS.items()
}


def italian_speech(text):
    def resolve(match):
        word = match.group()
        # Context windows bound the work even for long paragraphs.
        left = text[max(0, match.start() - 72):match.start()]
        right = text[match.end():match.end() + 72]
        choices = [accent for accent, before, after in CONTEXT_RULES[word.lower()]
                   if before.search(left) or after.search(right)]
        if len(choices) != 1:
            return word
        accent = choices[0]
        if word.isupper():
            return accent.upper()
        return accent.capitalize() if word.istitle() else accent

    text = ITALIAN_STRESS.sub(
        lambda match: match[0][:4] + ('À' if match[0][4].isupper() else 'à') + match[0][5:],
        text,
    )
    return spoken_it(OMOGRAPH_WORD.sub(resolve, text))


def lexicon():
    data=json.loads(LEXICON.read_text(encoding='utf-8')) if LEXICON.exists() else {}
    if not isinstance(data,dict): raise ValueError('Lessico pronunce non valido')
    return data


def speech_parts(text, language='auto', entries=None):
    if language not in LANGUAGES: raise ValueError('Lingua non supportata')
    if language == 'ipa-it':
        from ipa_reader import phoneme_input
        phoneme_input(text)
        return [{'text':text, 'language':'ipa-it'}]
    entries=lexicon() if entries is None else entries
    for key,item in entries.items():
        if not key or not isinstance(item,dict) or item.get('language') not in LANGUAGES or item['language']=='auto': raise ValueError('Voce del lessico non valida')
    keys=sorted(entries,key=len,reverse=True)
    pattern=re.compile('|'.join(re.escape(k) for k in keys)) if keys else None
    result=[]
    def plain(value):
        if not value.strip(): return
        if language!='auto':
            if language in ('el','ar') and re.search(r'[A-Za-z\u0100-\u024f]',value):
                raise ValueError('Traslitterazione ambigua: aggiungere una pronuncia in grafia originale o un WAV nel lessico.')
            result.append({'text':italian_speech(value) if language=='it' else value,'language':language}); return
        start=0
        for match in SCRIPT.finditer(value):
            plain_it(value[start:match.start()])
            result.append({'text':match.group(),'language':'ar' if '\u0600'<=match.group()[0]<='\u06ff' else 'el'})
            start=match.end()
        plain_it(value[start:])
    def plain_it(value):
        if not value.strip(): return
        if re.search(r'[\u0400-\u052f\u0590-\u05ff\u3040-\u30ff\u4e00-\u9fff\uac00-\ud7af]',value):
            raise ValueError('Scrittura non italiana: selezionare esplicitamente la lingua del segmento o aggiungere una pronuncia.')
        if re.search(r'[\u0250-\u02ff\u1d00-\u1d7f]|al-ḥamdu|lillāh|Dóxa|Theós|Barukh|ha-Shem|ilāha',value,re.I):
            raise ValueError('Espressione fonetica o traslitterata da verificare nel lessico pronunce; non verrà italianizzata automaticamente.')
        result.append({'text':italian_speech(value),'language':'it'})
    start=0
    if pattern:
        for match in pattern.finditer(text):
            plain(text[start:match.start()]); item=entries[match.group()]
            if item.get('audio'):
                path=Path(item['audio']); path=path if path.is_absolute() else LEXICON.parent/path
                if not path.is_file(): raise ValueError('WAV di pronuncia mancante')
                result.append({'text':match.group(),'language':item['language'],'audio':str(path.resolve())})
            else:
                native=item.get('text','')
                if not native: raise ValueError('Inserire la grafia originale o un audio verificato')
                if item['language'] in ('el','ar') and (re.search(r'[A-Za-z\u0100-\u02af]',native) or not SCRIPT.search(native)):
                    raise ValueError('Per greco/arabo inserire la grafia originale; per traslitterazioni o IPA usare un WAV verificato.')
                if item['language']=='ar' and re.search(r'[\u0370-\u03ff\u1f00-\u1fff]',native) or item['language']=='el' and re.search(r'[\u0600-\u06ff]',native):
                    raise ValueError('La scrittura della pronuncia non corrisponde alla lingua selezionata.')
                result.append({'text':native,'language':item['language']})
            start=match.end()
    plain(text[start:])
    return result
