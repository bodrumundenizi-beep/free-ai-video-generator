"""A map of Windows: every place a tech tip can open with a fixed command.

Layers: Settings pages (``ms-settings:`` links from Microsoft's documented list), Control
Panel pages (``control /name``), classic windows (``.cpl``), and tools and folders. Each entry
has the words people use for it, so a tip like "turn off the annoying startup programs" finds
Startup apps without any AI. The Smart writer then only chooses among the few entries found
here (or Windows' own search), so it can't invent a page.

Places that need admin rights or guard the PC's security are listed too, marked ``admin``, so
the app can say why it won't record them instead of guessing.

Line format: ``id | name | other words | how it opens | flags`` where "how it opens" is
``ms-settings:...``, ``control:Microsoft.X``, ``cpl:file.cpl``, ``shell:Folder``, ``run:program``
or ``app:AppUserModelID``, and flags may hold ``admin`` and ``win10`` / ``win11``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_MAP = """
# --- Settings: system -------------------------------------------------------------------------
display | Display | screen, resolution, scale, brightness, rotation, monitor, refresh rate, hdr, multiple displays, text size | ms-settings:display |
display-graphics | Graphics | gpu preference, graphics card per app, high performance gpu, hardware accelerated gpu scheduling, gpu scheduling, hags, auto hdr, windowed games optimizations, variable refresh rate | ms-settings:display-advancedgraphics |
nightlight | Night light | blue light, warm screen, eye strain, night mode, sleep better | ms-settings:nightlight |
sound | Sound | volume, speakers, headphones, output, input, audio, enhance audio, mono audio | ms-settings:sound |
sound-devices | Sound devices | all sound devices, disable speaker, audio devices | ms-settings:sound-devices |
volume-mixer | Volume mixer | app volume, per app sound, mixer | ms-settings:apps-volume |
default-mic | Microphone properties | mic, microphone volume, input level, default microphone | ms-settings:sound-defaultinputproperties |
default-speaker | Speaker properties | spatial sound, audio output format, default speaker | ms-settings:sound-defaultoutputproperties |
notifications | Notifications | alerts, popups, banners, do not disturb, notification sounds | ms-settings:notifications |
focus | Focus | focus assist, do not disturb, focus sessions, quiet hours | ms-settings:quiethours |
power | Power & battery | sleep, screen timeout, turn off display, power mode, energy saver, battery, when plugged in | ms-settings:powersleep |
battery-saver | Energy saver | battery saver, save battery, low battery | ms-settings:batterysaver |
battery-use | Battery usage | battery use per app, what drains battery | ms-settings:batterysaver-usagedetails |
energy-recommendations | Energy recommendations | save energy, eco, power recommendations | ms-settings:energyrecommendations | win11
storage | Storage | disk space, free up space, temporary files, cleanup, full drive, c drive full | ms-settings:storagesense |
storage-sense | Storage Sense | automatic cleanup, delete temp files automatically, recycle bin auto empty, empty recycle bin automatically, recycle bin empty itself, empty itself, delete old downloads | ms-settings:storagepolicies |
storage-recommendations | Cleanup recommendations | large files, unused apps, cleanup suggestions | ms-settings:storagerecommendations | win11
disks | Disks & volumes | partitions, drives, volumes, format drive, rename drive | ms-settings:disksandvolumes |
save-locations | Where new content is saved | default save location, save to d drive, downloads location | ms-settings:savelocations |
multitasking | Multitasking | snap windows, snap layouts, alt tab, shake to minimize, virtual desktops | ms-settings:multitasking |
clipboard | Clipboard | clipboard history, win v, copy paste history, sync clipboard | ms-settings:clipboard |
project-to-pc | Projecting to this PC | miracast, wireless display, cast to this pc | ms-settings:project |
remote-desktop | Remote Desktop | rdp, remote access, control from another pc | ms-settings:remotedesktop |
about | About | system info, pc name, specs, ram, processor, windows version, rename pc, device specifications | ms-settings:about |
shared-experiences | Nearby sharing | share between pcs, nearby share, cross device | ms-settings:crossdevice |
presence | Presence sensing | wake on approach, lock when away, human presence | ms-settings:presence | win11
activation | Activation | activate windows, product key, license, digital license | ms-settings:activation |
troubleshoot | Troubleshoot | fix problems, troubleshooters, something not working | ms-settings:troubleshoot |
recovery | Recovery | reset this pc, factory reset, go back, advanced startup, fresh start | ms-settings:recovery |
for-developers | For developers | developer mode, dev settings, end task in taskbar, sudo | ms-settings:developers |
optional-features | Optional features | add a feature, windows features, install feature | ms-settings:optionalfeatures |
# --- Settings: Bluetooth & devices --------------------------------------------------------------
bluetooth | Bluetooth & devices | pair, bluetooth, wireless headphones, connect device, add device | ms-settings:bluetooth |
connected-devices | Devices | connected devices, all devices, remove device | ms-settings:connecteddevices |
printers | Printers & scanners | printer, scanner, print, default printer, add printer | ms-settings:printers |
mouse | Mouse | mouse speed, pointer speed, scroll speed, primary button, mouse acceleration, enhance pointer precision | ms-settings:mousetouchpad |
touchpad | Touchpad | trackpad, gestures, tap to click, two finger scroll, three finger | ms-settings:devices-touchpad |
pen | Pen & Windows Ink | stylus, pen, handwriting | ms-settings:pen |
typing | Typing | autocorrect, text suggestions, spelling, keyboard typing | ms-settings:typing |
autoplay | AutoPlay | usb plug in, autoplay, auto open usb | ms-settings:autoplay |
usb | USB | usb notifications, usb power saving | ms-settings:usb |
camera | Cameras | webcam, camera settings, brightness camera | ms-settings:camera | win11
phone-link | Phone Link | phone, android, iphone, mobile devices, link phone | ms-settings:mobile-devices |
# --- Settings: network ---------------------------------------------------------------------------
network | Network & internet | internet, connection, network status, data usage | ms-settings:network-status |
wifi | Wi-Fi | wireless, wifi, hidden network, random hardware address | ms-settings:network-wifi |
known-networks | Known networks | forget network, saved wifi, manage known networks | ms-settings:network-wifisettings |
ethernet | Ethernet | cable, wired, dns, ip address, metered connection | ms-settings:network-ethernet |
network-advanced | Advanced network settings | network reset, adapters, data usage, network discovery, sharing | ms-settings:network-advancedsettings |
hotspot | Mobile hotspot | share internet, tethering, hotspot | ms-settings:network-mobilehotspot |
airplane | Airplane mode | flight mode, airplane | ms-settings:network-airplanemode |
vpn | VPN | vpn, private network | ms-settings:network-vpn |
proxy | Proxy | proxy server, proxy settings | ms-settings:network-proxy |
cellular | Cellular | mobile data, sim, lte, 5g | ms-settings:network-cellular |
dialup | Dial-up | dial up, modem connection | ms-settings:network-dialup |
# --- Settings: personalization -----------------------------------------------------------------
personalization | Personalization | customize, look, appearance | ms-settings:personalization |
background | Background | wallpaper, desktop background, slideshow, spotlight | ms-settings:personalization-background |
colors | Colors | dark mode, light mode, accent color, transparency, theme color, black theme | ms-settings:colors |
themes | Themes | theme, desktop icons, this pc icon, recycle bin icon, cursor theme | ms-settings:themes |
lock-screen | Lock screen | lock screen picture, spotlight, lock screen status, screen saver | ms-settings:lockscreen |
start | Start | start menu, recommended, recently added, most used, pins | ms-settings:personalization-start |
start-folders | Start folders | folders next to power button, settings in start, file explorer in start | ms-settings:personalization-start-places |
taskbar | Taskbar | taskbar, center icons, align left, widgets, search box, task view, hide taskbar, system tray, combine buttons | ms-settings:taskbar |
fonts | Fonts | install font, font size, typefaces | ms-settings:fonts |
text-input | Text input | touch keyboard theme, emoji panel, input | ms-settings:personalization-textinput |
touch-keyboard | Touch keyboard | on screen keyboard, touch keyboard | ms-settings:personalization-touchkeyboard |
dynamic-lighting | Dynamic Lighting | rgb, keyboard lights, lighting effects | ms-settings:personalization-lighting | win11
copilot-key | Copilot key | copilot key, remap copilot key | ms-settings:personalization-textinput-copilot-hardwarekey | win11
# --- Settings: apps --------------------------------------------------------------------------------
installed-apps | Installed apps | uninstall, remove app, apps and features, app size, reset app, repair app | ms-settings:appsfeatures |
default-apps | Default apps | default browser, open with, default program, file associations, pdf opens with | ms-settings:defaultapps |
startup-apps | Startup apps | startup programs, apps that start with windows, slow boot, launch on startup | ms-settings:startupapps |
apps-for-websites | Apps for websites | open links in app, website apps | ms-settings:appsforwebsites |
video-playback | Video playback | hdr video, video quality, battery video | ms-settings:videoplayback |
offline-maps | Offline maps | download maps, maps | ms-settings:maps |
# --- Settings: accounts --------------------------------------------------------------------------
your-info | Your info | account picture, microsoft account, local account | ms-settings:yourinfo |
email-accounts | Email & accounts | email, add account, mail accounts | ms-settings:emailandaccounts |
sign-in | Sign-in options | pin, password, windows hello, face, fingerprint, dynamic lock, require sign in | ms-settings:signinoptions |
family-users | Other users | add user, other people, family, guest account | ms-settings:otherusers |
work-school | Access work or school | work account, school account, organization | ms-settings:workplace |
sync | Windows backup | sync settings, remember my apps, backup settings | ms-settings:sync |
# --- Settings: time & language --------------------------------------------------------------------
date-time | Date & time | clock, time zone, set time automatically, sync time | ms-settings:dateandtime |
region | Region | country, regional format, date format, first day of week | ms-settings:regionformatting |
language | Language & region | display language, keyboard language, add language | ms-settings:regionlanguage |
keyboard | Keyboard | input method, keyboard layout, switch input, language bar | ms-settings:keyboard |
speech | Speech | speech language, voices, voice typing | ms-settings:speech |
# --- Settings: gaming ------------------------------------------------------------------------------
game-mode | Game Mode | game mode, gaming performance, fps boost | ms-settings:gaming-gamemode |
game-bar | Game Bar | xbox game bar, win g, overlay | ms-settings:gaming-gamebar |
captures | Captures | game clips, record game, screen recording, game dvr | ms-settings:gaming-gamedvr |
# --- Settings: accessibility -----------------------------------------------------------------------
text-size | Text size | bigger text, make text bigger, font scaling | ms-settings:easeofaccess-display |
visual-effects | Visual effects | animations, transparency, scrollbars always visible, dismiss notifications | ms-settings:easeofaccess-visualeffects |
mouse-pointer | Mouse pointer & touch | cursor size, cursor color, bigger mouse pointer | ms-settings:easeofaccess-mousepointer |
text-cursor | Text cursor | text cursor thickness, cursor indicator | ms-settings:easeofaccess-cursor |
magnifier | Magnifier | zoom screen, magnify, win plus | ms-settings:easeofaccess-magnifier |
color-filters | Color filters | colour blind, grayscale, invert colors | ms-settings:easeofaccess-colorfilter |
contrast-themes | Contrast themes | high contrast, contrast | ms-settings:easeofaccess-highcontrast |
narrator | Narrator | screen reader, read aloud, narrator | ms-settings:easeofaccess-narrator |
audio-accessibility | Audio accessibility | mono audio, flash screen for notifications | ms-settings:easeofaccess-audio |
captions | Captions | live captions, subtitles style | ms-settings:easeofaccess-closedcaptioning |
speech-access | Speech recognition | voice access, voice control, dictation | ms-settings:easeofaccess-speechrecognition |
keyboard-access | Keyboard accessibility | sticky keys, filter keys, toggle keys, on screen keyboard, print screen key snipping, underline access keys | ms-settings:easeofaccess-keyboard |
mouse-keys | Mouse keys | mouse keys, control mouse with keyboard | ms-settings:easeofaccess-mouse |
eye-control | Eye control | eye tracking, eye control | ms-settings:easeofaccess-eyecontrol |
hearing-devices | Hearing devices | hearing aids | ms-settings:easeofaccess-hearingaids | win11
# --- Settings: privacy ---------------------------------------------------------------------------
privacy | Privacy & security | privacy, permissions, tracking, ads | ms-settings:privacy |
privacy-general | General privacy | advertising id, personalized ads, suggested content, tracking app launches | ms-settings:privacy-general |
privacy-location | Location | location services, gps, location history | ms-settings:privacy-location |
privacy-camera | Camera access | camera permission, which apps use camera | ms-settings:privacy-webcam |
privacy-microphone | Microphone access | microphone permission, which apps use mic | ms-settings:privacy-microphone |
privacy-notifications | Notification access | apps reading notifications | ms-settings:privacy-notifications |
privacy-diagnostics | Diagnostics & feedback | telemetry, diagnostic data, feedback frequency, tailored experiences | ms-settings:privacy-feedback |
activity-history | Activity history | timeline, activity history, clear history | ms-settings:privacy-activityhistory |
privacy-speech | Online speech recognition | speech privacy | ms-settings:privacy-speech |
privacy-inking | Inking & typing personalization | typing data, custom dictionary | ms-settings:privacy-speechtyping |
privacy-files | File system access | apps access files, documents access | ms-settings:privacy-broadfilesystemaccess |
privacy-contacts | Contacts access | contacts permission | ms-settings:privacy-contacts |
privacy-calendar | Calendar access | calendar permission | ms-settings:privacy-calendar |
privacy-screenshots | Screenshot access | screenshot permission, screen capture permission | ms-settings:privacy-graphicscaptureprogrammatic |
app-diagnostics | App diagnostics | app diagnostic info | ms-settings:privacy-appdiagnostics |
search-permissions | Search permissions | safe search, search history, cloud search | ms-settings:search-permissions |
searching-windows | Searching Windows | indexing, enhanced search, search locations | ms-settings:search-moredetails |
find-my-device | Find my device | lost laptop, track device | ms-settings:findmydevice |
encryption | Device encryption | bitlocker, encryption | ms-settings:deviceencryption | admin
windows-security | Windows Security | antivirus, defender, virus scan, firewall, ransomware | ms-settings:windowsdefender | admin
# --- Settings: Windows Update --------------------------------------------------------------------
windows-update | Windows Update | updates, check for updates, pause updates, update | ms-settings:windowsupdate |
update-options | Advanced update options | active hours, restart notifications, delivery optimization, optional updates | ms-settings:windowsupdate-options |
update-history | Update history | installed updates, uninstall update | ms-settings:windowsupdate-history |
optional-updates | Optional updates | driver updates, optional updates | ms-settings:windowsupdate-optionalupdates |
active-hours | Active hours | dont restart while using, active hours | ms-settings:windowsupdate-activehours |
delivery-optimization | Delivery Optimization | download from other pcs, update bandwidth, limit update speed | ms-settings:delivery-optimization |
insider | Windows Insider Program | insider, beta, preview builds | ms-settings:windowsinsider-optin |
# --- Control Panel --------------------------------------------------------------------------------
power-options | Power Options | power plan, high performance, ultimate performance, balanced, power saver, what power buttons do, power button, fast startup, lid close, hibernate, hibernate option | control:Microsoft.PowerOptions |
sound-classic | Sound (classic) | playback devices, recording devices, sound scheme, communications ducking, stereo mix | cpl:mmsys.cpl |
mouse-classic | Mouse Properties | double click speed, pointer trails, mouse pointer scheme, enhance pointer precision, mouse acceleration | cpl:main.cpl |
keyboard-classic | Keyboard Properties | key repeat rate, repeat delay, cursor blink | control:Microsoft.Keyboard |
network-connections | Network Connections | network adapters, adapter settings, change adapter options, dns server, ipv4 | cpl:ncpa.cpl |
network-sharing | Network and Sharing Center | sharing center, network sharing, homegroup | control:Microsoft.NetworkAndSharingCenter |
system-properties | System Properties | advanced system settings, performance options, visual effects best performance, virtual memory, page file, environment variables, system restore point | cpl:sysdm.cpl |
programs-features | Programs and Features | uninstall a program, classic uninstall, turn windows features on or off | cpl:appwiz.cpl |
internet-options | Internet Options | internet properties, clear browsing data ie, proxy classic | cpl:inetcpl.cpl |
date-time-classic | Date and Time (classic) | additional clocks, internet time | cpl:timedate.cpl |
region-classic | Region (classic) | administrative language, system locale, number format | cpl:intl.cpl |
game-controllers | Game Controllers | gamepad, joystick, controller calibration | cpl:joy.cpl |
devices-printers | Devices and Printers | devices and printers, printer properties | control:Microsoft.DevicesAndPrinters |
file-explorer-options | File Explorer Options | folder options, show hidden files, file extensions, show file extensions, open file explorer to this pc | control:Microsoft.FolderOptions |
indexing-options | Indexing Options | search index, rebuild index | control:Microsoft.IndexingOptions |
color-management | Color Management | color profile, icc profile, monitor calibration | control:Microsoft.ColorManagement |
display-calibration | Display color calibration | calibrate display, gamma | run:dccw |
ease-of-access-center | Ease of Access Center | ease of access, accessibility classic | control:Microsoft.EaseOfAccessCenter |
sync-center | Sync Center | offline files, sync center | control:Microsoft.SyncCenter |
file-history | File History | backup files, file history | control:Microsoft.FileHistory |
backup-restore | Backup and Restore (Windows 7) | system image, backup and restore | control:Microsoft.BackupAndRestore |
autoplay-classic | AutoPlay (classic) | autoplay defaults | control:Microsoft.AutoPlay |
default-programs | Default Programs | set default programs, file types | control:Microsoft.DefaultPrograms |
security-maintenance | Security and Maintenance | maintenance, reliability, action center, problem reports | control:Microsoft.ActionCenter |
troubleshooting-classic | Troubleshooting (classic) | troubleshooting, fix problems classic | control:Microsoft.Troubleshooting |
recovery-classic | Recovery (classic) | restore point, open system restore, recovery drive | control:Microsoft.Recovery |
phone-modem | Phone and Modem | modem, dialing rules | control:Microsoft.PhoneAndModem |
remoteapp | RemoteApp and Desktop Connections | remoteapp | control:Microsoft.RemoteAppAndDesktopConnections |
speech-classic | Speech Recognition (classic) | train speech, microphone setup | control:Microsoft.SpeechRecognition |
storage-spaces | Storage Spaces | storage pool, mirror drives | control:Microsoft.StorageSpaces |
windows-tools | Windows Tools | administrative tools, all tools | control:Microsoft.AdministrativeTools |
user-accounts-classic | User Accounts | change account type, user accounts | control:Microsoft.UserAccounts |
mobility-center | Windows Mobility Center | mobility center, presentation settings | control:Microsoft.MobilityCenter |
work-folders | Work Folders | work folders | control:Microsoft.WorkFolders |
credential-manager | Credential Manager | saved passwords, windows credentials | control:Microsoft.CredentialManager | admin
firewall | Windows Defender Firewall | firewall, allow app through firewall, ports | control:Microsoft.WindowsFirewall | admin
bitlocker | BitLocker Drive Encryption | bitlocker, encrypt drive | control:Microsoft.BitLockerDriveEncryption | admin
uac | User Account Control | uac, admin prompt, user account control | run:UserAccountControlSettings | admin
iscsi | iSCSI Initiator | iscsi | control:Microsoft.iSCSIInitiator | admin
# --- Tools and folders ----------------------------------------------------------------------------
task-manager | Task Manager | end task, cpu usage, ram usage, processes, startup impact, performance graph, frozen app | run:taskmgr |
resource-monitor | Resource Monitor | resource monitor, disk activity, network activity per app | run:resmon |
system-information | System Information | msinfo, bios version, motherboard, full specs | run:msinfo32 |
dxdiag | DirectX Diagnostic Tool | dxdiag, directx version, graphics driver info | run:dxdiag |
disk-cleanup | Disk Cleanup | cleanmgr, clean disk, delete temporary files classic | run:cleanmgr |
defragment | Optimize Drives | defrag, trim ssd, optimize drives | run:dfrgui |
on-screen-keyboard | On-Screen Keyboard | osk, keyboard on screen | run:osk |
character-map | Character Map | special characters, symbols | run:charmap |
steps-recorder | Snipping Tool | screenshot, snip, screen capture, print screen | app:Microsoft.ScreenSketch_8wekyb3d8bbwe!App |
remote-desktop-client | Remote Desktop Connection | mstsc, connect to remote pc | run:mstsc |
winver | About Windows | winver, windows build, exact version | run:winver |
volume-mixer-classic | Volume Mixer (classic) | sndvol, classic volume mixer | run:sndvol |
this-pc | This PC | my computer, drives, file explorer this pc | shell:MyComputerFolder |
startup-folder | Startup folder | startup folder, run at login, shell startup | shell:Startup |
sendto-folder | Send To folder | send to menu, add to send to | shell:SendTo |
fonts-folder | Fonts folder | installed fonts folder | shell:Fonts |
downloads-folder | Downloads | downloads folder | shell:Downloads |
recycle-bin | Recycle Bin | recycle bin, deleted files, restore deleted | shell:RecycleBinFolder |
all-apps-folder | All apps folder | every installed app, apps folder | shell:AppsFolder |
quick-access | Quick access | home, quick access, pinned folders | shell:::{679f85cb-0220-4080-b29b-5540cc05aab6} |
network-folder | Network | network computers, shared folders | shell:NetworkPlacesFolder |
device-manager | Device Manager | drivers, update driver, disable device, unknown device, roll back driver | run:devmgmt.msc | admin
disk-management | Disk Management | partition, format, extend volume, assign drive letter | run:diskmgmt.msc | admin
services | Services | windows services, disable service, stop service | run:services.msc | admin
event-viewer | Event Viewer | crash logs, error logs, event log | run:eventvwr.msc | admin
system-configuration | System Configuration | msconfig, safe boot, boot options | run:msconfig | admin
registry-editor | Registry Editor | regedit, registry | run:regedit | admin
group-policy | Local Group Policy Editor | gpedit, group policy, policies | run:gpedit.msc | admin
computer-management | Computer Management | computer management, local users and groups | run:compmgmt.msc | admin
task-scheduler | Task Scheduler | scheduled tasks, run at time | run:taskschd.msc | admin
performance-monitor | Performance Monitor | perfmon, reliability, performance counters | run:perfmon | admin
"""


@dataclass(frozen=True)
class Place:
    id: str
    name: str
    words: tuple        # the name and every other word people use, lower case
    opens: str          # as written in the map: ms-settings:..., control:..., cpl:...
    admin: bool         # needs admin rights or guards security: explained, never recorded
    windows: str        # "10", "11" or "" for both

    def command(self) -> list:
        """The command that opens this place, run without a console window."""
        kind, _, what = self.opens.partition(":")
        if kind == "ms-settings":
            return ["explorer", self.opens]
        if kind == "control":
            return ["control", "/name", what]
        if kind == "cpl":
            return ["control", what]
        if kind in ("shell", "app"):
            return ["explorer", "shell:" + what if kind == "shell" else
                    "shell:AppsFolder\\" + what]
        if kind == "run":
            return ["cmd", "/c", "start", "", what]
        raise ValueError(f"Unknown way to open {self.id}: {self.opens}")


def _parse(text: str) -> list[Place]:
    places = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [part.strip() for part in line.split("|")]
        parts += [""] * (5 - len(parts))
        ident, name, other, opens, flags = parts
        words = tuple(w.strip().lower() for w in [name, *other.split(",")] if w.strip())
        version = "11" if "win11" in flags else "10" if "win10" in flags else ""
        places.append(Place(ident, name, words, opens, "admin" in flags, version))
    return places


PLACES = _parse(_MAP)
BY_ID = {place.id: place for place in PLACES}

# Pages whose switches the app never flips itself, even for a moment: the screen could go
# dark, the network or mouse could go away, the PC could need a restart, an account or app
# could change. The user clicks there, as before.
NEVER_AUTO = {
    "display", "display-graphics", "sound-devices", "default-mic", "default-speaker",
    "power", "recovery", "activation", "for-developers", "optional-features", "remote-desktop",
    "bluetooth", "connected-devices", "printers", "mouse", "touchpad", "pen", "typing",
    "network", "wifi", "known-networks", "ethernet", "network-advanced", "hotspot", "airplane",
    "vpn", "proxy", "cellular", "dialup", "installed-apps", "default-apps", "apps-for-websites",
    "your-info", "email-accounts", "sign-in", "family-users", "work-school", "sync",
    "date-time", "region", "language", "keyboard", "text-size", "mouse-pointer", "text-cursor",
    "magnifier", "color-filters", "contrast-themes", "narrator", "speech-access", "captions",
    "keyboard-access", "mouse-keys", "eye-control", "privacy-camera", "privacy-microphone",
    "privacy-location", "find-my-device", "windows-update", "update-options",
    "optional-updates", "active-hours", "delivery-optimization", "insider",
}
# ...and switches named like these, on any page.
NEVER_AUTO_NAMES = re.compile(
    r"restart|reboot|narrator|magnif|live caption|contrast|filter|sticky|bluetooth|wi-?fi|airplane|vpn|"
    r"\bhdr\b|touchpad|primary|swap|developer|remote|password|sign[- ]in|encrypt|location|"
    r"camera|microphone|tamper|firewall|defender|update", re.I)

_WORD = re.compile(r"[a-z0-9]+")
# Words that say nothing about where a setting lives.
_STOP = {"how", "to", "the", "a", "an", "on", "off", "in", "of", "for", "your", "my", "and",
         "turn", "enable", "disable", "change", "set", "make", "use", "windows", "pc", "it",
         "with", "from", "is", "be", "can", "you", "i", "do", "this", "that", "what", "settings",
         "setting", "open", "find", "way", "best", "get", "fix", "stop", "start", "up", "too"}


def _stem(word: str) -> str:
    for end in ("ing", "es", "s"):
        if len(word) > len(end) + 3 and word.endswith(end):
            return word[: -len(end)]
    return word


def _terms(text: str) -> set:
    return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def search(text: str, n: int = 8) -> list[Place]:
    """The places that best match a tip, best first (empty if nothing matches at all).

    A whole phrase from a place's words ("night light", "startup apps") counts most; single
    words count less, and the place's own name a little more than its other words.
    """
    asked = " " + " ".join(_WORD.findall(text.lower())) + " "
    terms = _terms(text)
    scored = []
    for place in PLACES:
        score = 0.0
        # The page's own name asked for as is counts most, stop words and all: "how to change
        # Start" (all stop words) and "Troubleshoot" (lost to troubleshooting-classic) (2026-10-24).
        name = " ".join(_WORD.findall(place.name.lower()))
        if name and f" {name} " in asked:
            score += 3.0
        for i, phrase in enumerate(place.words):
            words = _terms(phrase)
            if not words:
                continue
            if len(phrase.split()) > 1 and f" {' '.join(_WORD.findall(phrase))} " in asked:
                score += 3.0 * len(words)          # the whole phrase was asked for
            shared = len(words & terms)
            if shared:
                score += shared / len(words) * (1.5 if i == 0 else 1.0)
        if score > 0:
            scored.append((score, place))
    scored.sort(key=lambda pair: -pair[0])
    return [place for _score, place in scored[:n]]
