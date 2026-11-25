"""Look and feel: one place for every colour so text, cards and buttons always match."""
from __future__ import annotations

ACCENTS = {
    "Teal": "#19e6d2",       # neon aqua (default)
    "Blue": "#4d8dff",
    "Violet": "#9b7bff",
    "Orange": "#ff9a3d",
    "Rose": "#ff5c8a",
    "Cyan": "#22d3ee",
    "Plasma": "#c15bff",
    "Lime": "#a3ff5c",
}

# Futuristic look: deep space blue, glass-like cards, neon accents.
PALETTES = {
    "dark": dict(bg="#060a15", side="#09101f", top="#09101f", card="#0c1630", card2="#111f3e", input="#070e1f",
                 border="#1c3159", text="#e9f4ff", muted="#93b2d6", hover="#182a50", disabled_bg="#0e1832",
                 disabled_fg="#4f6489", on_accent="#02141a", track="#16274b"),
    "light": dict(bg="#edf3fc", side="#ffffff", top="#ffffff", card="#ffffff", card2="#e5eefa", input="#f6f9fe",
                  border="#c3d3ea", text="#0b1730", muted="#40597d", hover="#d9e6f8", disabled_bg="#e3ebf6",
                  disabled_fg="#93a5bf", on_accent="#02141a", track="#d3e0f2"),
}


def palette(theme: str = "dark", accent: str = "Teal") -> dict:
    p = dict(PALETTES.get(theme, PALETTES["dark"]))
    p["accent"] = ACCENTS.get(accent, ACCENTS["Teal"])
    return p


def _mix(hex_a: str, hex_b: str, t: float) -> str:
    a = [int(hex_a[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(hex_b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))


def stylesheet(theme: str = "dark", accent: str = "Teal", text_px: int = 15) -> str:
    c = palette(theme, accent)
    c["accent_hover"] = _mix(c["accent"], "#ffffff", 0.18)
    c["accent_soft"] = _mix(c["accent"], c["card"], 0.82)
    c["accent_press"] = _mix(c["accent"], "#000000", 0.15)
    c["accent2"] = _mix(c["accent"], "#8b6cff", 0.55)          # second colour of the neon gradient
    c["accent_edge"] = _mix(c["border"], c["accent"], 0.28)    # card edges glow slightly in the accent colour
    c["accent_dim"] = _mix(c["accent"], c["card"], 0.90)
    c["px"] = max(11, min(24, int(text_px)))
    c["px_small"] = max(11, c["px"] - 1)
    c["px_title"] = c["px"] + 12
    try:
        from .platform_info import font_stack
        c["font"] = font_stack()
    except Exception:
        c["font"] = "'Segoe UI Variable Text','Segoe UI',Arial"
    c["px_section"] = c["px"] + 3
    return """
* {{ font-family: {font}; font-size: {px}px; }}
QMainWindow, QDialog, QMessageBox, QInputDialog, QWidget#root, QWidget#page {{ background: {bg}; color: {text}; }}
QWidget {{ color: {text}; }}
QScrollArea, QScrollArea > QWidget > QWidget {{ background: transparent; border: 0; }}
QScrollArea QWidget#qt_scrollarea_viewport {{ background: {bg}; }}
QToolTip {{ color: {text}; background: {card2}; border: 1px solid {border}; padding: 4px 6px; }}

QFrame#topbar {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {top}, stop:1 {card}); border-bottom: 1px solid {accent_edge}; }}
QFrame#sidebar {{ background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 {side}, stop:1 {bg}); border-right: 1px solid {accent_edge}; }}
QFrame#panel, QFrame#card {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {card}, stop:1 {card2}); border: 1px solid {accent_edge}; border-radius: 16px; }}
QFrame#meterdeck {{ background: {card2}; border: 1px solid {accent_edge}; border-radius: 12px; }}
QLabel#valuebox {{ background: {input}; border: 1px solid {border}; border-radius: 9px; padding: 7px 8px; font-weight: 650; }}
QFrame#dropzone {{ background: {input}; border: 2px dashed {border}; border-radius: 16px; }}
QFrame#dropzone[hot="true"] {{ border: 2px dashed {accent}; background: {accent_soft}; }}
QFrame#cardinner {{ background: {card2}; border: 1px solid {border}; border-radius: 12px; }}
QLabel {{ color: {text}; background: transparent; }}
QLabel#muted {{ color: {muted}; font-size: {px_small}px; }}
QLabel#title {{ color: {text}; font-size: {px_title}px; font-weight: 700; }}
QLabel#sectiontitle {{ color: {text}; font-size: {px_section}px; font-weight: 650; }}
QLabel#fieldlabel {{ color: {muted}; font-size: {px_small}px; font-weight: 700; }}
QLabel#brand {{ color: {text}; font-size: {px_section}px; font-weight: 700; }}
QLabel#navhead {{ color: {muted}; font-size: {px_small}px; font-weight: 700; letter-spacing: 1px; padding: 6px 12px 2px 12px; }}
QLabel#badge {{ background: {accent_soft}; color: {accent}; border-radius: 10px; padding: 3px 10px; font-weight: 650; font-size: {px_small}px; }}
QLabel#stat {{ color: {muted}; font-size: {px_small}px; }}
QLabel#footer {{ color: {muted}; background: {top}; border-top: 1px solid {border}; padding: 7px 20px; font-size: {px_small}px; }}

QPushButton {{ background: {card2}; color: {text}; border: 1px solid {border}; border-radius: 10px; padding: 8px 14px; font-weight: 600; }}
QPushButton:hover {{ background: {hover}; border-color: {accent_edge}; }}
QPushButton:pressed {{ background: {border}; }}
QPushButton:disabled {{ background: {disabled_bg}; color: {disabled_fg}; border-color: {disabled_bg}; }}
QPushButton#primary {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); color: {on_accent}; border: 0; font-weight: 700; padding: 10px 18px; }}
QPushButton#primary:hover {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent_hover}, stop:1 {accent}); }}
QPushButton#primary:pressed {{ background: {accent_press}; }}
QPushButton#primary:disabled {{ background: {disabled_bg}; color: {disabled_fg}; }}
QPushButton#nav {{ text-align: left; border: 0; background: transparent; padding: 10px 14px; border-radius: 10px; color: {muted}; }}
QPushButton#nav:hover {{ background: {hover}; color: {text}; }}
QPushButton#nav:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent_soft}, stop:1 transparent); color: {accent}; font-weight: 700; border-left: 3px solid {accent}; }}
QPushButton#nav:disabled {{ color: {disabled_fg}; background: transparent; }}
QPushButton#chip {{ background: {card2}; border: 1px solid {border}; border-radius: 16px; padding: 7px 16px; color: {text}; }}
QPushButton#chip:hover {{ background: {hover}; }}
QPushButton#chip:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); color: {on_accent}; border: 1px solid {accent}; font-weight: 700; }}
QPushButton#tab {{ background: transparent; border: 0; border-radius: 12px; padding: 9px 18px; color: {muted}; font-weight: 700; }}
QPushButton#tab:hover {{ background: {hover}; color: {text}; }}
QPushButton#tab:checked {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); color: {on_accent}; }}
QPushButton#fold {{ text-align: left; background: transparent; border: 0; padding: 2px 0; color: {text}; font-size: {px_section}px; font-weight: 650; }}
QPushButton#fold:hover {{ color: {accent}; background: transparent; }}
QPushButton#navfold {{ text-align: left; background: transparent; border: 0; color: {muted}; font-size: {px_small}px; font-weight: 700; letter-spacing: 1px; padding: 6px 12px 2px 12px; }}
QPushButton#navfold:hover {{ color: {accent}; background: transparent; }}
QPushButton#icon {{ background: transparent; border: 1px solid {border}; border-radius: 10px; padding: 0; font-size: 16px; }}
QPushButton#icon:hover {{ background: {hover}; }}
QLabel#chiplabel {{ background: {card2}; border: 1px solid {border}; border-radius: 10px; padding: 2px 9px; font-size: {px_small}px; }}
QLabel#statusdone {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); color: {on_accent}; border-radius: 10px; padding: 3px 11px; font-weight: 700; font-size: {px_small}px; }}
QLabel#statuswait {{ background: {card2}; color: {muted}; border: 1px solid {border}; border-radius: 10px; padding: 3px 11px; font-weight: 700; font-size: {px_small}px; }}
QLabel#statusbad {{ background: #7f1d1d; color: #ffffff; border-radius: 10px; padding: 3px 11px; font-weight: 700; font-size: {px_small}px; }}
QPushButton#danger {{ background: #7f1d1d; color: #ffffff; border: 0; font-weight: 700; }}
QPushButton#danger:hover {{ background: #b91c1c; }}
QPushButton#danger:disabled {{ background: {disabled_bg}; color: {disabled_fg}; }}
QPushButton#ghost {{ background: transparent; border: 1px solid {border}; }}
QPushButton#ghost:hover {{ background: {hover}; }}
QPushButton#star {{ font-size: {px_section}px; padding: 4px 10px; background: transparent; border: 1px solid {border}; }}
QPushButton#star:checked {{ color: #f5b73a; border-color: #f5b73a; }}

QTextEdit, QPlainTextEdit {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 12px; padding: 12px; selection-background-color: {accent}; selection-color: {on_accent}; }}
QTextEdit:focus, QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {accent}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 9px; padding: 7px 10px; min-height: 20px; selection-background-color: {accent}; selection-color: {on_accent}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled {{ background: {disabled_bg}; color: {disabled_fg}; }}
QComboBox::drop-down {{ border: 0; width: 26px; }}
QComboBox QAbstractItemView {{ background: {card}; color: {text}; border: 1px solid {border}; selection-background-color: {accent}; selection-color: {on_accent}; outline: 0; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 18px; border: 0; background: transparent; }}

QCheckBox {{ spacing: 9px; color: {text}; }}
QCheckBox:disabled {{ color: {disabled_fg}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {muted}; border-radius: 5px; background: {input}; }}
QCheckBox::indicator:checked {{ background: {accent}; border: 1px solid {accent}; }}

QSlider::groove:horizontal {{ height: 6px; background: {track}; border-radius: 3px; }}
QSlider::sub-page:horizontal {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); border-radius: 3px; }}
QSlider::handle:horizontal {{ background: {text}; width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; border: 2px solid {accent}; }}
QProgressBar {{ border: 0; border-radius: 5px; background: {track}; height: 9px; }}
QProgressBar::chunk {{ background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 {accent}, stop:1 {accent2}); border-radius: 5px; }}

QTabWidget::pane {{ border: 0; }}
QTabBar::tab {{ background: transparent; color: {muted}; padding: 9px 16px; border-radius: 10px; margin-right: 4px; }}
QTabBar::tab:hover {{ background: {hover}; color: {text}; }}
QTabBar::tab:selected {{ background: {accent_soft}; color: {accent}; font-weight: 700; }}

QListWidget {{ background: {input}; color: {text}; border: 1px solid {border}; border-radius: 12px; padding: 6px; outline: 0; }}
QListWidget::item {{ padding: 9px 10px; border-radius: 8px; }}
QListWidget::item:selected {{ background: {accent_soft}; color: {text}; }}
QListWidget::item:hover {{ background: {hover}; }}
QTableWidget, QTableView {{ background: {card}; alternate-background-color: {card2}; color: {text}; gridline-color: {border}; border: 1px solid {border}; border-radius: 12px; selection-background-color: {accent_soft}; selection-color: {text}; }}
QTableWidget::item {{ padding: 4px; }}
QHeaderView::section {{ background: {card2}; color: {muted}; border: 0; border-bottom: 1px solid {border}; padding: 8px; font-weight: 650; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: 5px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {accent}; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {border}; border-radius: 5px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QDialogButtonBox {{ background: transparent; }}
""".format(**c)


def apply_palette(app, theme: str = "dark", accent: str = "Teal") -> None:
    """Qt palette so native dialogs and unstyled widgets follow the same colours."""
    from PySide6.QtGui import QPalette, QColor
    c = palette(theme, accent)
    pal = QPalette()
    for role, key in (("Window", "bg"), ("WindowText", "text"), ("Base", "input"), ("AlternateBase", "card2"),
                      ("Text", "text"), ("Button", "card2"), ("ButtonText", "text"), ("ToolTipBase", "card2"),
                      ("ToolTipText", "text"), ("Highlight", "accent"), ("HighlightedText", "on_accent"),
                      ("PlaceholderText", "muted"), ("Link", "accent")):
        pal.setColor(getattr(QPalette.ColorRole, role), QColor(c[key]))
    app.setPalette(pal)
