"""Italian spoken numbers without modifying saved manuscript text."""
from datetime import date
import re

VERSION = 'it-numeri-date-1'
MONTHS = ('gennaio', 'febbraio', 'marzo', 'aprile', 'maggio', 'giugno',
          'luglio', 'agosto', 'settembre', 'ottobre', 'novembre', 'dicembre')
SMALL = ('zero', 'uno', 'due', 'tre', 'quattro', 'cinque', 'sei', 'sette', 'otto', 'nove',
         'dieci', 'undici', 'dodici', 'tredici', 'quattordici', 'quindici', 'sedici',
         'diciassette', 'diciotto', 'diciannove')
TENS = ('', '', 'venti', 'trenta', 'quaranta', 'cinquanta', 'sessanta', 'settanta', 'ottanta', 'novanta')


def integer_it(number):
    if not 0 <= number < 1_000_000:
        raise ValueError('Intero fuori dal perimetro della normalizzazione vocale')
    if number < 20:
        result = SMALL[number]
    elif number < 100:
        tens, units = divmod(number, 10)
        base = TENS[tens][:-1] if units in (1, 8) else TENS[tens]
        result = base + (SMALL[units] if units else '')
    elif number < 1000:
        hundreds, rest = divmod(number, 100)
        base = (SMALL[hundreds] if hundreds > 1 else '') + 'cento'
        if rest == 8 or 80 <= rest < 90:
            base = base[:-1]
        result = base + (integer_it(rest) if rest else '')
    else:
        thousands, rest = divmod(number, 1000)
        base = 'mille' if thousands == 1 else integer_it(thousands) + 'mila'
        # An internal compound does not retain the final stress mark of ventitré.
        base = base.replace('tré', 'tre')
        result = base + (integer_it(rest) if rest else '')
    if number > 3 and result.endswith('tre'):
        result = result[:-1] + 'é'
    return result


def spoken_it(text):
    def numeric_date(match):
        day, month, year = map(int, match.groups())
        try:
            date(year, month, day)
        except ValueError:
            return match.group()
        return ('primo' if day == 1 else integer_it(day)) + ' ' + MONTHS[month - 1] + ' ' + integer_it(year)

    text = re.sub(r'(?<![\w/])([0-3]?\d)/([01]?\d)/(\d{4})(?![\w/])', numeric_date, text)
    text = re.sub(r'\b1(?:[°º])?(?=\s+(?:' + '|'.join(MONTHS) + r')\b)', 'primo', text, flags=re.I)

    def number(match):
        token = match.group()
        # The verified corpus contains whole-number years and days. Decimals,
        # codes, leading zeros and alphanumeric identifiers are not reinterpreted.
        if len(token) > 6 or (len(token) > 1 and token.startswith('0')):
            return token
        return integer_it(int(token))

    return re.sub(r'(?<![\w.,/])(?<![A-Za-z]-)\d+(?![\w.,/]\d|[\w/])', number, text)
