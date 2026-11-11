"""Local editorial profiles with validated settings and per-segment delivery."""
import json
from pathlib import Path

CONFIG = Path(__file__).with_name('profili_vocali.json')
DEFAULTS = {
    'Narratore': ('CosimoM', -1, 0), 'Da assegnare': ('CosimoM', -1, 0),
    'Yusuf': ('CosimoM', -1, -2), 'Baldo': ('CosimoM', -2, -4),
    'Aureliano': ('CosimoM', 0, -3), 'Anselmo': ('CosimoM', -2, -1),
    'Nicasio': ('CosimoM', 0, 3), 'Sālim': ('CosimoM', 0, 1),
    'Alasia': ('ElsaM', 0, 1), 'Eudocia': ('ElsaM', -1, -2),
    'Miriam': ('ElsaM', -1, 0), 'Altro': ('ElsaM', 0, 0),
}
TONES = {'Neutro': (0, 0), 'Sommesso': (-1, -1), 'Concitato': (1, 1),
         'Fermo': (-1, 0), 'Esitante': (-2, 0), 'Calmo': (-2, -1),
         'Urgente': (2, 1), 'Dolente': (-2, -2), 'Solenne': (-2, -1),
         'Ironico': (0, 1), 'Interrogativo': (0, 2), 'Stanco': (-3, -2),
         'Rassicurante': (-1, -1), 'Adirato': (2, 2), 'Sussurrato': (-2, -2)}
TONE_SPEEDS = dict(zip(TONES, (1, .94, 1.08, .97, .90, .92, 1.14, .88, .88, 1.02, 1.01, .85, .94, 1.12, .90)))
for _name, _speed, _rate, _pitch in [
    ('Allarmato',1.12,2,2),('Urlante',1.10,2,3),('Sofferente',.86,-3,-2),
    ('Impaurito',1.06,1,2),('Teso',1.04,1,1),('Tremante',.94,-1,1),
    ('Triste',.88,-2,-2),('Malinconico',.91,-2,-1),('Affettuoso',.96,-1,0),
    ('Gioioso',1.08,1,2),('Entusiasta',1.12,2,2),('Sorpreso',1.07,1,2),
    ('Dubbioso',.93,-1,1),('Confuso',.91,-2,1),('Sospettoso',.94,-1,-1),
    ('Diffidente',.95,-1,-1),('Sprezzante',.97,-1,-2),('Sarcastico',1.01,0,1),
    ('Minaccioso',.92,-2,-3),('Imperioso',.98,0,-2),('Supplichevole',.91,-2,1),
    ('Disperato',1.06,1,2),('Rassegnato',.86,-3,-2),('Meditativo',.88,-2,-1),
    ('Sognante',.90,-2,0),('Confidenziale',.93,-1,-1),('Rituale',.86,-2,-1),
    ('Scandito',.90,-2,0),('Rapido',1.18,3,0),('Lento',.82,-3,0)]:
    TONES[_name]=(_rate,_pitch); TONE_SPEEDS[_name]=_speed


def tone_names(value):
    names = value if isinstance(value,list) else [p.strip() for p in value.split(' + ')] if isinstance(value,str) else []
    if not names or any(n not in TONES for n in names): raise ValueError('Combinazione di toni non valida')
    return [n for n in dict.fromkeys(names) if n!='Neutro'] or ['Neutro']


def tone_label(value):
    return ' + '.join(tone_names(value))


def delivery(value):
    names=tone_names(value)
    # Average combined tones to avoid exponential speed and pitch changes.
    speed=max(.75,min(1.25,sum(TONE_SPEEDS[n] for n in names)/len(names)))
    gain=.5 if 'Sussurrato' in names else .8 if 'Sommesso' in names else 1.12 if 'Urlante' in names else 1
    return speed,gain


def validate(data):
    if data.get('version') != 1 or not isinstance(data.get('characters'), dict):
        raise ValueError('Configurazione vocale non valida')
    for name in ('Narratore', 'Da assegnare', 'Altro'):
        if name not in data['characters']:
            raise ValueError('Profilo obbligatorio mancante: ' + name)
    for name, item in data['characters'].items():
        if not isinstance(name, str) or not name.strip() or not isinstance(item, dict):
            raise ValueError('Nome o profilo non valido')
        if item.get('voice') not in ('CosimoM', 'ElsaM'):
            raise ValueError('Timbro locale sconosciuto: ' + name)
        if item.get('gender','non_specificato') not in ('maschio','femmina','misto','non_specificato'):
            raise ValueError('Genere del profilo non valido')
        if item.get('engine', 'windows') not in ('windows', 'qwen3'):
            raise ValueError('Motore sconosciuto: ' + name)
        if item.get('engine') == 'qwen3':
            if not isinstance(item.get('reference'), str) or not Path(item['reference']).is_file():
                raise ValueError('Campione Qwen mancante: ' + name)
        for key in ('rate', 'pitch'):
            if type(item.get(key)) is not int or not -10 <= item[key] <= 10:
                raise ValueError('Parametro fuori intervallo: ' + name + '/' + key)
        if item.get('status') not in ('provvisorio', 'approvato'):
            raise ValueError('Stato editoriale non valido: ' + name)
        for key in ('description', 'evidence'):
            if not isinstance(item.get(key), str):
                raise ValueError('Descrizione o provenienza mancante: ' + name)
        if item.get('kind', 'personaggio') not in ('personaggio', 'secondario', 'fuori_campo', 'gruppo'):
            raise ValueError('Tipo profilo non valido')
        if item.get('kind') == 'gruppo':
            members = item.get('members', [])
            if not isinstance(members, list) or not 2 <= len(members) <= 6 or len(set(members)) != len(members):
                raise ValueError('Un gruppo richiede da due a sei profili distinti')
            if any(m not in data['characters'] or m == name or data['characters'][m].get('kind') == 'gruppo' for m in members):
                raise ValueError('Membri del gruppo mancanti o gruppi annidati')
    return data


def load():
    if CONFIG.exists():
        return validate(json.loads(CONFIG.read_text(encoding='utf-8-sig')))
    return validate({'version': 1, 'characters': {
        name: {'voice': voice, 'rate': rate, 'pitch': pitch,
               'status': 'provvisorio', 'description': '',
               'evidence': 'Parametri tecnici preesistenti; nessuna descrizione narrativa approvata.'}
        for name, (voice, rate, pitch) in DEFAULTS.items()}})


def resolve(data, role, tone='Neutro'):
    validate(data)
    names=tone_names(tone)
    if role not in data['characters']:
        raise ValueError('Profilo sconosciuto: ' + role)
    item = data['characters'][role]
    dr=round(sum(TONES[n][0] for n in names)/len(names))
    dp=round(sum(TONES[n][1] for n in names)/len(names))
    return item['voice'], max(-10, min(10, item['rate'] + dr)), max(-10, min(10, item['pitch'] + dp))


def main():
    import argparse
    from audiolibro import save
    parser = argparse.ArgumentParser(description='Configura una voce locale per qualsiasi personaggio; descrizioni editoriali separate dal manoscritto.')
    parser.add_argument('name', nargs='?')
    parser.add_argument('--voice', choices=['CosimoM', 'ElsaM'])
    parser.add_argument('--gender',choices=['maschio','femmina','misto','non_specificato'])
    parser.add_argument('--engine', choices=['windows', 'qwen3'])
    parser.add_argument('--reference', help='Percorso assoluto del JSON di un campione sintetico Qwen')
    parser.add_argument('--rate', type=int)
    parser.add_argument('--pitch', type=int)
    parser.add_argument('--description')
    parser.add_argument('--evidence')
    parser.add_argument('--status', choices=['provvisorio', 'approvato'])
    args = parser.parse_args()
    data = load()
    if args.name:
        item = data['characters'].setdefault(args.name, {'voice': 'CosimoM', 'rate': 0, 'pitch': 0,
            'description': '', 'evidence': 'Nuovo profilo tecnico, da verificare con il canone.', 'status': 'provvisorio'})
        for key in ('voice', 'gender', 'engine', 'reference', 'rate', 'pitch', 'description', 'evidence', 'status'):
            if getattr(args, key) is not None:
                item[key] = getattr(args, key)
        validate(data)
        save(CONFIG, data)
    print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
