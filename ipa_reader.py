"""Explicit multilingual IPA-to-eSpeak input, without orthographic guessing."""
import unicodedata

PHONES = {char: char for char in 'aeioubptdkfvszmnljwh'}
PHONES.update({'ɡ':'g', 'g':'g', 'ɛ':'E', 'ɔ':'O', 'ʃ':'S', 'ʒ':'Z',
               'ɲ':'n^', 'ʎ':'l^', 'ŋ':'N', 'ɱ':'M', 'r':'R', 'ɾ':'*',
               'ʁ':'Q"', 'ħ':'H', 'ʕ':'A', 'ð':'D', 'θ':'T',
               's̪':'s[', 'ʔ':'?', 'ɹ':'r', 'x':'x', 'χ':'X', 'ɣ':'Q',
               'tʃ':'tS', 'dʒ':'dZ', 'ts':'ts', 'dz':'dz'})
KEYS = sorted(PHONES, key=len, reverse=True)
VOWELS = set('aeiouɛɔ')
ARABIC_TABLE = {'ħ', 'ʕ', 's̪'}


def phoneme_input(text, gender='non_specificato'):
    """Compile IPA to phonemes and generated, bounded SSML voice spans.

    Pharyngeals and dental /s/ require the Arabic table. Selecting this resource
    does not infer a source language. Caller must enable PHONEMES and SSML.
    """
    text = unicodedata.normalize('NFC', text.strip())
    if (text.startswith('/') and text.endswith('/')) or (text.startswith('[') and text.endswith(']')):
        text = text[1:-1]
    text = text.replace('\u0361', '').replace('\u035c', '')
    result, word, stress = [], [], ''
    word_phones = set()
    position = 0
    spoken = False

    def flush():
        nonlocal word
        if word:
            encoded = '[[' + ''.join(word) + ']]'
            if word_phones & ARABIC_TABLE:
                # Refuse mixtures missing from this table; eSpeak drops unknowns.
                if word_phones & {'ɛ', 'ɔ', 'ɣ'}:
                    raise ValueError('IPA: combinazione di fonemi non disponibile nella stessa tabella eSpeak.')
                attributes = 'name="ar" gender="female" variant="3"' if gender == 'femmina' else 'name="ar"'
                # Spaces prevent eSpeak 1.52 from speaking closing tags as text.
                result.append(f'<voice {attributes}> {encoded} </voice> ')
            else:
                result.append(encoded)
            word = []
            word_phones.clear()

    while position < len(text):
        char = text[position]
        if char in 'ˈˌ':
            if stress:
                raise ValueError('IPA: accenti consecutivi senza vocale.')
            stress = "'" if char == 'ˈ' else ','
            position += 1
            continue
        if char == 'ː':
            if not word or word[-1] == ':':
                raise ValueError('IPA: lunghezza senza fonema precedente.')
            word.append(':')
            position += 1
            continue
        if char == '.' and position + 1 < len(text) and not text[position + 1].isspace():
            position += 1
            continue
        if char.isspace() or char in '.,;:!?«»“”|‖':
            if stress:
                raise ValueError('IPA: accento senza vocale prima del confine.')
            flush()
            result.append(' ' if char.isspace() or char in '«»“”' else ',' if char == '|' else '.' if char == '‖' else char)
            position += 1
            continue
        phone = next((key for key in KEYS if text.startswith(key, position)), None)
        if phone is None:
            raise ValueError(f'IPA: simbolo non supportato {char!r} alla posizione {position + 1}.')
        word_phones.add(phone)
        if phone in VOWELS:
            word.append(stress)
            stress = ''
            spoken = True
        word.append(PHONES[phone])
        position += len(phone)
    if stress:
        raise ValueError('IPA: accento finale senza vocale.')
    flush()
    if not spoken:
        raise ValueError('IPA: nessuna vocale pronunciabile.')
    return ''.join(result)
