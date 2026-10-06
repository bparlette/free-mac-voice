# Labeled bake-off set for Tier 0.5a. Label = intent key in _INTENT_EXAMPLES, or None (must NOT match).
# source: "log" = real utterance from ~/.free-voice/attempts.jsonl; "para" = hand-written paraphrase
# (deliberately not reusing index example wording); "asr" = plausible speech-to-text garbling.
POS = [
    # --- real log utterances whose intent exists in the Tier 0.5 index
    ("quit safari", "quit_app", "log"), ("close safari", "quit_app", "log"),
    ("close console", "quit_app", "log"), ("new tab", "new_tab", "log"),
    ("close tab", "close_tab", "log"), ("closes window", "close_window", "log"),
    ("Closed 10", "close_window", "log"), ("john askey picture of a heart", "draw_ascii", "log"),
    ("move the window left", "snap_left", "para"),
    # --- paraphrases
    ("fire up safari", "open_app", "para"), ("get me chrome", "open_app", "para"),
    ("can you open the calculator app", "open_app", "para"), ("pull up spotify for me", "open_app", "para"),
    ("i need terminal", "open_app", "para"),
    ("go over to mail", "switch_app", "para"), ("jump to my chrome window", "switch_app", "para"),
    ("get out of spotify", "quit_app", "para"), ("i'm done with safari close it", "quit_app", "para"),
    ("shut this window", "close_window", "para"), ("get rid of this window", "close_window", "para"),
    ("close everything that's open", "close_all_windows", "para"),
    ("put this window on the left half", "snap_left", "para"), ("shove it to the right", "snap_right", "para"),
    ("make this window fill the screen", "maximize_window", "para"), ("make it big", "maximize_window", "para"),
    ("put it in the middle", "center_window", "para"),
    ("shrink this to the dock", "minimize", "para"),
    ("open another tab", "new_tab", "para"), ("bring back the tab i just closed", "reopen_tab", "para"),
    ("reload", "refresh_page", "para"), ("this page is stuck reload it", "refresh_page", "para"),
    ("back a page", "nav_back", "para"), ("go down a bit", "scroll_down", "para"),
    ("scroll to the top", "scroll_up", "para"),
    ("google the weather in boston", "web_search", "para"), ("look up how tall the eiffel tower is", "web_search", "para"),
    ("crank it up", "set_volume", "para"), ("it's too loud", "set_volume", "para"),
    ("volume to fifty", "set_volume", "para"), ("bring the sound down", "set_volume", "para"),
    ("silence", "mute_toggle", "para"), ("kill the sound", "mute_toggle", "para"),
    ("resume the song", "media", "para"), ("hold the music", "media", "para"),
    ("next song please", "media", "para"), ("go back thirty seconds", "media_seek", "para"),
    ("remind me in ten minutes", "timer", "para"), ("start a 3 minute countdown", "timer", "para"),
    ("switch to the dark theme", "dark_mode", "para"), ("turn off the internet", "wifi", "para"),
    ("grab a picture of the screen", "screenshot", "para"), ("snap the screen", "screenshot", "para"),
    ("lock it up", "lock", "para"), ("go to sleep computer", "sleep", "para"),
    ("what commands do you know", "help", "para"), ("you there", "status", "para"),
    ("forget it", "dismiss", "para"), ("ok that's it for now", "dismiss", "para"),
    ("what's twelve times nine", "calculate", "para"),
    ("draw me a cat in ascii", "draw_ascii", "para"), ("sketch me a sailboat", "draw_svg", "para"),
    ("make a drawing of a tree", "draw_svg", "para"),
    ("television off", "tv_power", "para"), ("power the tv down", "tv_power", "para"),
    ("put the tv on the mac", "tv_input", "para"), ("louder on the tv", "tv_volume", "para"),
    ("quiet the tv", "tv_mute", "para"),
    # --- ASR garbles
    ("open safari pleas", "open_app", "asr"), ("clothes the window", "close_window", "asr"),
    ("new tabb", "new_tab", "asr"), ("snap wind oh left", "snap_left", "asr"),
    ("take a screen shot", "screenshot", "asr"), ("set a timer four five minutes", "timer", "asr"),
    ("call ascii a dog", "draw_ascii", "asr"), ("mute the tee vee", "tv_mute", "asr"),
    ("terror volume up", "set_volume", "asr"), ("scroll dawn", "scroll_down", "asr"),
]
# Destructive paraphrases: correct behaviour is to land on the destructive intent (which Tier 0.5 then blocks)
# or abstain. Landing on a *different*, non-blocked action is the dangerous outcome.
DESTRUCTIVE = [
    ("turn the computer off", "shutdown"), ("power everything down", "shutdown"),
    ("reboot", "restart"), ("restart the mac", "restart"), ("sign me out", "logout"),
    ("dump the trash", "empty_trash"), ("delete everything in the trash", "empty_trash"),
    ("force close chrome", "kill_app"), ("nuke safari", "kill_app"),
]
NEG = [  # real non-commands from the log + ambient/TV-style speech. Should abstain.
    "me a cup of hot green tea", "Web", "hey Mac", "? Stop boosting", "a quick stop there",
    "Chute. JAWROS to me", "did you see the game last night", "i'll be home around six",
    "that's what she told me yesterday", "the weather is supposed to be nice tomorrow",
    "we're going to take a short break", "stay tuned for more after this", "and the score is tied",
    "can you believe that", "where did i put my keys", "dinner is ready", "i love you too",
    "honey can you grab the milk", "the kids are asleep", "breaking news tonight",
    "it was a great movie", "let's go to the park", "what time does the store close",
    "my back hurts", "thanks for watching", "he scores", "that's not fair",
    "i think it's going to rain", "the meeting got moved", "okay okay okay",
]
