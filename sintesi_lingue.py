"""Local eSpeak NG fallback for languages Qwen does not support."""
import ctypes
import math
import threading

_LOCK = threading.Lock()
_SESSION = None


def validate_ipa_delivery(settings):
    """Validate optional native delivery controls without loading audio packages."""
    limits = {'rate_wpm': (80, 300), 'pitch': (0, 100), 'range': (0, 100), 'warmth_db': (-9, 0)}
    if not isinstance(settings, dict) or set(settings) - set(limits):
        raise ValueError('Parametri di resa IPA non validi.')
    for key, value in settings.items():
        low, high = limits[key]
        if (type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high
                or (key != 'warmth_db' and type(value) is not int)):
            raise ValueError('Parametro di resa IPA fuori intervallo: ' + key)
    return dict(settings)


def soften_ipa(samples, rate, gain_db):
    """Apply a bounded gentle high shelf; do not shift formants or phonemes."""
    import numpy as np
    from scipy.signal import butter, sosfilt
    if not gain_db:
        return samples
    low = sosfilt(butter(2, 600, fs=rate, output='sos'), samples)
    softened = low + (samples - low) * 10 ** (gain_db / 20)
    if not np.isfinite(softened).all():
        raise ValueError('Filtro IPA: audio non valido.')
    peak = np.max(np.abs(softened))
    return (softened * min(1, .98 / max(peak, 1e-12))).astype(np.float32)


class _NativeSession:
    """Own one process-global engine and keep its callback alive until termination."""
    def __init__(self):
        from espeakng_loader import load_library, get_data_path
        self.engine = engine = load_library()
        if engine is None:
            raise RuntimeError('Backend eSpeak NG non disponibile')
        engine.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        self.rate = engine.espeak_Initialize(2, 0, get_data_path().encode('utf-8'), 0)
        if self.rate <= 0:
            raise RuntimeError('Inizializzazione eSpeak fallita')
        self.chunks = []
        callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_short), ctypes.c_int, ctypes.c_void_p)
        def receive(samples, count, events):
            if samples and count:
                self.chunks.append(ctypes.string_at(samples, count * 2))
            return 0
        self.callback = callback_type(receive)
        engine.espeak_SetSynthCallback(self.callback)
        engine.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
        engine.espeak_SetParameter.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        engine.espeak_Synth.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint, ctypes.c_int,
                                       ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p]

    def close(self):
        # The native library must stop before Python can release the callback.
        self.engine.espeak_Terminate()
        self.chunks.clear()

    def synthesize(self, text, language, gender, settings, is_ipa):
        import numpy as np
        engine = self.engine
        self.chunks.clear()
        if is_ipa and '<voice name="ar"' in text:
            if engine.espeak_SetVoiceByName(b'ar') != 0:
                raise ValueError('IPA: risorsa fonemica eSpeak araba non disponibile.')
        voice = language + ('+f3' if gender == 'femmina' else '')
        if engine.espeak_SetVoiceByName(voice.encode()) != 0:
            raise ValueError('Voce eSpeak non disponibile: ' + voice)
        for parameter, value in ((1, settings.get('rate_wpm', 175)),
                                 (3, settings.get('pitch', 50)), (4, settings.get('range', 50))):
            if engine.espeak_SetParameter(parameter, value, 0) != 0:
                raise RuntimeError('Configurazione della resa vocale non riuscita.')
        data = text.encode('utf-8')
        flags = 1 | (0x100 | 0x10 if is_ipa else 0)
        if engine.espeak_Synth(ctypes.c_char_p(data), len(data)+1, 0, 1, 0, flags, None, None) != 0:
            raise RuntimeError('Sintesi linguistica fallita')
        if engine.espeak_Synchronize() != 0:
            raise RuntimeError('Completamento della sintesi linguistica non riuscito.')
        samples = np.frombuffer(b''.join(self.chunks), dtype=np.int16).astype(np.float32) / 32768
        self.chunks.clear()
        return samples, self.rate


def _close_native_session():
    """Caller holds _LOCK; also exposed for isolated runtime tests."""
    global _SESSION
    if _SESSION is not None:
        _SESSION.close()
        _SESSION = None


def synthesize_native(text, language, gender='non_specificato', ipa_delivery=None):
    """Serialize native use and retry silence once with a fresh engine instance."""
    global _SESSION
    import numpy as np
    is_ipa = language == 'ipa-it'
    settings = validate_ipa_delivery({} if ipa_delivery is None else ipa_delivery) if is_ipa else {}
    if is_ipa:
        from ipa_reader import phoneme_input
        text = phoneme_input(text, gender)
        language = 'it'
    with _LOCK:
        for attempt in range(2):
            if _SESSION is None:
                _SESSION = _NativeSession()
            try:
                samples, rate = _SESSION.synthesize(text, language, gender, settings, is_ipa)
                if not np.isfinite(samples).all():
                    raise ValueError('Audio linguistico non valido.')
                if samples.size:
                    samples = soften_ipa(samples, rate, settings.get('warmth_db', 0))
                    if np.max(np.abs(samples)) >= .00001:
                        return samples, rate
            except Exception:
                _close_native_session()
                raise
            _close_native_session()
        raise RuntimeError('eSpeak NG ha prodotto audio vuoto o silenzioso anche dopo il recupero (2 tentativi).')
