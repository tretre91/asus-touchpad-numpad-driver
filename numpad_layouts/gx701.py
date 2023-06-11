from evdev import ecodes

cols = 4
rows = 5
# Subtract 0.3 (a third key) as the UX581L has about a third key space at the top
top_offset = 0

keys = [
    [ecodes.KEY_CALC, ecodes.KEY_KPSLASH, ecodes.KEY_KPASTERISK, ecodes.KEY_KPMINUS],
    [ecodes.KEY_KP7, ecodes.KEY_KP8, ecodes.KEY_KP9, ecodes.KEY_KPPLUS],
    [ecodes.KEY_KP4, ecodes.KEY_KP5, ecodes.KEY_KP6, ecodes.KEY_KPPLUS],
    [ecodes.KEY_KP1, ecodes.KEY_KP2, ecodes.KEY_KP3, ecodes.KEY_KPENTER],
    [ecodes.KEY_KP0, ecodes.KEY_KP0, ecodes.KEY_KPDOT, ecodes.KEY_KPENTER]
]
