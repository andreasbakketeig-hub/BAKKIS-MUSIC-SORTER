# dmgbuild settings for the installer window: the app on the left, Applications on the right.
#   dmgbuild -s packaging/dmg_settings.py -D app="dist/BAKKIS MUSIC SORTER.app" "BAKKIS MUSIC SORTER" out.dmg
# Icon positions match tools/make_dmg_background.py.
import os.path

application = defines.get("app", "dist/BAKKIS MUSIC SORTER.app")  # noqa: F821 - provided by dmgbuild
appname = os.path.basename(application)
here = os.path.dirname(os.path.abspath(globals().get("__file__", "packaging/dmg_settings.py")))
root = os.path.dirname(here) if os.path.basename(here) == "packaging" else os.getcwd()

format = "UDZO"  # compressed, read-only
filesystem = "HFS+"
files = [application]
symlinks = {"Applications": "/Applications"}
icon = os.path.join(root, "assets", "icon", "AppIcon.icns")  # the volume icon
background = os.path.join(root, "assets", "dmg", "background.png")  # background@2x.png is picked up for Retina
show_status_bar = False
show_tab_view = False
show_toolbar = False
show_pathbar = False
show_sidebar = False
window_rect = ((200, 120), (660, 400))
default_view = "icon-view"
icon_size = 112
text_size = 13
icon_locations = {appname: (170, 205), "Applications": (490, 205)}
