"""Equalizzazione parametrica dei campioni vocali PCM a 16 bit."""
from array import array
import math
import sys


BANDS = (('Corpo', 250), ('Durezza', 3200), ('Definizione', 6000))
FLAT = (0, 0, 0)
SOFT = (1, -3, 0)
GAIN_LIMIT = 6
VERSION = 1


def validate_gains(gains):
    gains = tuple(gains)
    if len(gains) != len(BANDS) or any(type(gain) is not int or abs(gain) > GAIN_LIMIT for gain in gains):
        raise ValueError('Equalizzatore: servono tre guadagni interi fra −6 e +6 dB.')
    return gains


def equalize_pcm(data, sample_rate, channels, gains):
    if gains == FLAT or not data:
        return data
    pcm = array('h')
    pcm.frombytes(data)
    if sys.byteorder != 'little':
        pcm.byteswap()
    samples = array('d', pcm)

    for (_, frequency), gain in zip(BANDS, gains):
        if not gain or frequency >= sample_rate / 2:
            continue
        # Peaking EQ, Robert Bristow-Johnson, W3C Audio EQ Cookbook (2021), §2.
        # https://www.w3.org/TR/2021/NOTE-audio-eq-cookbook-20210608/#biquad-coefs
        omega = math.tau * frequency / sample_rate
        alpha = math.sin(omega) / (2 * 0.9)
        amplitude = 10 ** (gain / 40)
        a0 = 1 + alpha / amplitude
        b0 = (1 + alpha * amplitude) / a0
        b1 = a1 = -2 * math.cos(omega) / a0
        b2 = (1 - alpha * amplitude) / a0
        a2 = (1 - alpha / amplitude) / a0
        for channel in range(channels):
            z1 = z2 = 0.0
            for index in range(channel, len(samples), channels):
                sample = samples[index]
                output = b0 * sample + z1
                z1 = b1 * sample - a1 * output + z2
                z2 = b2 * sample - a2 * output
                samples[index] = output

    peak = max(map(abs, samples))
    scale = min(1.0, 32767 / peak) if peak else 1.0
    pcm = array('h', (round(sample * scale) for sample in samples))
    if sys.byteorder != 'little':
        pcm.byteswap()
    return pcm.tobytes()


def applies_to(role):
    return role not in ('Narratore', 'Da assegnare')
