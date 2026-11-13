"""Strict Italian IPA-to-eSpeak phoneme input, without orthographic guessing."""
import re
import unicodedata

PHONES = {char: char for char in 'aeioubptdkfvszmnlrjw'}
PHONES.update({'ɡ':'g', 'g':'g', 'ɛ':'E', 'ɔ':'O', 'ʃ':'S', 'ʒ':'Z',
               'ɲ':'n^', 'ʎ':'l^', 'ŋ':'N', 'ɱ':'M', 'ɾ':'r',
               'tʃ':'tS', 'dʒ':'dZ', 'ts':'ts', 'dz':'dz'})
KEYS = sorted(PHONES, key=len, reverse=True)
VOWELS = set('aeiouɛɔ')


def phoneme_input(text):
    """Accept broad Italian IPA, rejecting unknown symbols before synthesis."""
    text = unicodedata.normalize('NFC', text.strip())
    if (text.startswith('/') and text.endswith('/')) or (text.startswith('[') and text.endswith(']')):
        text = text[1:-1]
    text = text.replace('\u0361', '').replace('\u035c', '')
    result, word, stress = [], [], ''
    position = 0
    spoken = False

    def flush():
        nonlocal word
        if word:
            result.append('[[' + ''.join(word) + ']]')
            word = []

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
            raise ValueError(f'IPA italiano: simbolo non supportato {char!r} alla posizione {position + 1}.')
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
