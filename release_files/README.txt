BONES HIGHLIGHT SCOUT - BETA
============================
Finds the best moments in a stream or gameplay recording, lets you review and trim
them, and exports them as clips or vertical shorts.

This is a test version. It may have bugs. Your videos are never changed; clips are
saved as new files.


GETTING STARTED
---------------
1. Unzip the WHOLE folder first. It won't run from inside the zip.
2. Open the folder and double-click BonesHighlightScout.exe.
3. Windows may say "Windows protected your PC", because this test version isn't
   signed yet. Click "More info", then "Run anyway". Your antivirus may also scan it
   the first time, so the first start can take a little while.
4. Drag a video onto the window, then press SCAN.

Needs: Windows 10 or 11 (64-bit). No install and no internet needed. Nothing else to
download. Everything stays in this folder, so it can run from a USB drive. To remove
it, just delete the folder.


HOW TO USE IT
-------------
- DETECTION: "Hype moments (AI)" looks for laughter, screams, hype phrases and chat
  spikes. The first scan with speech takes about 1 minute per hour of video (more on
  older PCs). "Sudden spikes" and "Loud moments" just use loudness.
- Each clip shows tags saying why it was picked. Nothing here predicts views: it points
  at reactions and you decide what's good.
- PREVIEW plays a clip. Space = play/pause, arrows = 5 seconds, Page Up/Down = other clips.
- VERTICAL SHORT LAYOUT sets up a 9:16 short. Boxes on the video choose what to show.
  The preview chooses where it goes. Each clip can have its own layout.
- Choose an EXPORT type and press RENDER SELECTED. Clips go in a folder next to your video.
- Chat replays are optional: Twitch (TwitchDownloader JSON) or YouTube (yt-dlp live_chat).


WHAT TO TEST
------------
Please try it on your own recordings, especially:
- Long recordings (1 hour or more), and different games
- Did it find good moments? What did it miss? Did it pick boring ones?
- Vertical shorts: do they look like the preview?
- Does sound play in the preview, on your speakers or headset?
- Anything slow, confusing or broken


REPORTING A PROBLEM
-------------------
Report problems and ideas here (a free GitHub account is needed):

    https://github.com/boneSk8er5-lab/BonesHighlightScout/issues

Say what you did, what you expected and what happened. A screenshot helps.
If something went wrong, please attach this file if it exists:

    data\error.log   (in this folder)

It holds technical error details only, and may include file names and folder paths
from your PC. Issues are public, so have a look first and remove anything private.


KNOWN LIMITS
------------
- Windows only.
- The CAPTION STUDIO button opens Bones' Caption Studio if you have it. The download
  link isn't ready yet.
- This folder is large because the speech and sound AI models and the video tools
  are all included.


See THIRD-PARTY-NOTICES.txt for the open-source software this includes.
