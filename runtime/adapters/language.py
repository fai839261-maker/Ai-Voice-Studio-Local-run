"""Engine-specific language routing; no guess of Latin-script language."""
from functools import lru_cache
KOKORO_LANG={'a':'en-us','b':'en-gb','j':'ja','z':'zh','e':'es','f':'fr-fr','h':'hi','i':'it','p':'pt-br'}
QWEN_LANG=('Auto','Chinese','English','Japanese','Korean','German','French','Russian','Portuguese','Spanish','Italian')

def kokoro_language(voice):
    if len(voice)<4 or voice[0] not in KOKORO_LANG or voice[1] not in 'fm' or voice[2]!='_':raise ValueError('Unsupported Kokoro voice: '+voice)
    return KOKORO_LANG[voice[0]]

@lru_cache(maxsize=9)
def g2p_for(lang):
    from misaki import espeak
    if lang in ('en-us','en-gb'):
        from misaki import en
        british=lang=='en-gb'
        return en.G2P(trf=False,british=british,fallback=espeak.EspeakFallback(british=british))
    if lang=='ja':
        from misaki import ja
        return ja.JAG2P()
    if lang=='zh':
        from misaki import zh
        return zh.ZHG2P(version=None,en_callable=lambda text:g2p_for('en-us')(text)[0])
    return espeak.EspeakG2P(language=lang)

def phonemes(text,voice):
    lang=kokoro_language(voice)
    try:result=g2p_for(lang)(text)[0]
    except Exception as exc:raise RuntimeError(f'Kokoro {lang} phonemizer failed. Use AI Setup > Check to repair language dependencies: {exc}') from exc
    if not isinstance(result,str) or not result.strip():raise ValueError('No pronounceable text for the selected Kokoro language.')
    return result

def qwen_language(value):
    lookup={x.lower():x for x in QWEN_LANG}
    if str(value or 'Auto').lower() not in lookup:raise ValueError('Unsupported Qwen language: '+str(value))
    return lookup[str(value or 'Auto').lower()]
