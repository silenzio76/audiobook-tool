"""Local eSpeak NG fallback for languages Qwen does not support."""
import ctypes


def synthesize_native(text, language, gender='non_specificato'):
    is_ipa = language == 'ipa-it'
    if is_ipa:
        from ipa_reader import phoneme_input
        text = phoneme_input(text)
        language = 'it'
    import numpy as np
    from espeakng_loader import load_library,get_data_path
    engine=load_library()
    if engine is None: raise RuntimeError('Backend eSpeak NG non disponibile')
    engine.espeak_Initialize.argtypes=[ctypes.c_int,ctypes.c_int,ctypes.c_char_p,ctypes.c_int]
    rate=engine.espeak_Initialize(2,0,get_data_path().encode('utf-8'),0)
    if rate<=0: raise RuntimeError('Inizializzazione eSpeak fallita')
    chunks=[]
    callback_type=ctypes.CFUNCTYPE(ctypes.c_int,ctypes.POINTER(ctypes.c_short),ctypes.c_int,ctypes.c_void_p)
    def receive(samples,count,events):
        if samples and count: chunks.append(ctypes.string_at(samples,count*2))
        return 0
    callback=callback_type(receive)
    engine.espeak_SetSynthCallback(callback)
    engine.espeak_SetVoiceByName.argtypes=[ctypes.c_char_p]
    voice=language+('+f3' if gender=='femmina' else '')
    if engine.espeak_SetVoiceByName(voice.encode())!=0: raise ValueError('Voce eSpeak non disponibile: '+voice)
    data=text.encode('utf-8')
    engine.espeak_Synth.argtypes=[ctypes.c_void_p,ctypes.c_size_t,ctypes.c_uint,ctypes.c_int,ctypes.c_uint,ctypes.c_uint,ctypes.c_void_p,ctypes.c_void_p]
    flags = 1 | (0x100 if is_ipa else 0)
    if engine.espeak_Synth(ctypes.c_char_p(data),len(data)+1,0,1,0,flags,None,None)!=0: raise RuntimeError('Sintesi linguistica fallita')
    engine.espeak_Synchronize()
    if not chunks: raise RuntimeError('Nessun audio linguistico prodotto')
    return np.frombuffer(b''.join(chunks),dtype=np.int16).astype(np.float32)/32768,rate
