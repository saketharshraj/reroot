"""Narration + edit for the ReRoot demo (v4). Cues in {braces} attach to the word that follows them.

  {g:name}        gfx event for the scene's drawer
  {src:MARK+s}    screen: the recording must be at this moment when the next word is spoken
                  (an earlier moment than the previous anchor plays the footage in reverse)
  {cam:ROI}       screen: camera moves to this region (starts just before the word)
  {hl:ID}         screen: highlight on (replaces the previous one); {hl:off} clears it

Scenes sharing a "group" are voiced as one continuous take and split at their first words.
"""

VOICE = {"lang": "en-IN", "name": "Laomedeia", "model": "gemini-2.5-pro-tts", "rate": 1.06}
STYLE = ("You are a young Indian woman presenting the startup she built to hackathon judges. Speak like a real person who is "
         "genuinely excited and curious about it: warm, bright, energetic and conversational, with natural rhythm, emphasis "
         "and a smile in your voice. Keep a brisk, lively pace and flow from one sentence straight into the next, without long pauses.")
REC_DIR = "rec3"

CHAPTERS = [("Find planting sites", "Sites"), ("Write the plan", "Plan"), ("Monitor & alert", "Monitor"),
            ("Screen a data centre", "Data centre"), ("Verify the lakes", "Lakes")]

SCENES = [
    {"kind": "gfx", "draw": "hook", "group": "intro", "text":
     "{g:q}Quick question. When a {g:forest}forest is cut down for a {g:road}highway in India, the law says you have to "
     "{g:plant}plant it back somewhere else. But who actually checks that those saplings {g:survive}survive? And it's not just trees."},
    {"kind": "gfx", "draw": "water", "group": "intro", "text":
     "Water works the same way. {g:ai}AI runs on water, and the industry leaders are stepping up. Google {g:gallons}replenished "
     "7.7 billion gallons in 2025, {g:pledge}78 percent of its use, on the way to 120 percent, through {g:lakes}165 water projects. "
     "That's a huge commitment. So at that scale, {g:ask}how do you check that every lake is actually filling up?"},
    {"kind": "gfx", "draw": "badkhal", "group": "intro", "text":
     "Take {g:t0}Badkhal Lake, near Delhi. {g:t1}Dry for years. Then came a revival, and by {g:t2}2024, the water was back! "
     "But by {g:t3}2025, it's choked with green. {g:stamp}Funding a lake isn't proof of water."},
    {"kind": "gfx", "draw": "logo", "group": "intro", "text":
     "That's why we built {g:logo}ReRoot. Plant trees where they'll survive, {g:tag2}then prove they did."},
    {"kind": "gfx", "draw": "journey", "group": "intro", "tail": 0.6, "text":
     "Let me show you, {g:stops}in five stops."},

    {"kind": "screen", "chapter": 0, "src_start": "start", "src_end": "plan_type-0.3", "tail": 0.3, "text":
     "{cam:full}This is ReRoot Studio: Gemini on the left, a live satellite map on the right. "
     "{cam:roles}{hl:roles}{src:dev_start}I'm a highway contractor, so I just pick my {src:dev_send}role, {hl:off}{cam:chat}and Gemini "
     "takes it from there. {src:dev_send+3}It plans its own steps, {hl:steps}{src:dev_steps_first+0.2}loads our fourteen kilometre "
     "bypass, measures it from space with Earth Engine, and scores every one hectare cell with our Vertex AI survival "
     "{src:dev_done-0.4}model. {cam:full}{hl:off}{src:dev_done+0.6}Seconds later: this road clears a hundred and six hectares of trees, "
     "so we owe a hundred and ten back. {cam:sites}{hl:sites}{src:dev_done+2.6}ReRoot found eleven sites, a hundred and fifteen "
     "hectares, and not one of them on farmland. {cam:full}{hl:off}{src:dev_site1-0.7}Click site {src:dev_site1+0.2}one, and "
     "{cam:site1}{hl:site1}{src:dev_site1+2.6}it tells you why: degraded land next to existing forest, top three percent survival "
     "odds, and native species like neem and khejri."},

    {"kind": "screen", "chapter": 1, "src_start": "plan_type-0.2", "src_end": "plan_scroll-0.2", "tail": 0.3, "text":
     "{cam:composer}Need the paperwork? {src:plan_send}One line: write the plan. {cam:full}{src:plan_done+0.4}Seconds later, a "
     "complete compensatory planting plan, {src:plan_scroll}from the satellite map to species, schedule, "
     "{src:plan2_scroll+2.2}monitoring and risks. {src:plan_scroll-0.2}And right at the top, {cam:kpis}{hl:kpis}one lakh twenty six "
     "thousand saplings across eleven sites."},

    {"kind": "screen", "chapter": 2, "src_start": "mon_type-0.2", "src_end": "mon_close_phone-0.4", "tail": 0.3,
     "extra_audio": {"path": "assets/voice_note_cut.wav", "gap": 0.2, "gain": 0.95, "dur": 5.0}, "text":
     "{cam:composer}But planting is only half the job. {src:mon_send}Months later, ReRoot is still watching. "
     "{cam:chat}{hl:monsteps}{src:mon_steps_first+0.2}Gemini checks every plantation's satellite greenness against a healthy one its "
     "age, plus live rain and soil {src:mon_done-0.4}moisture. {cam:full}{hl:monlist}{src:mon_done+0.6}Pali scrubland? Seventy five "
     "percent of the green it should have, and no rain coming. {hl:phone}So Gemini writes an alert in Hindi, "
     "{cam:phone}{src:mon_english-0.5}and the caretaker gets this voice note."},

    {"kind": "screen", "chapter": 3, "src_start": "dc_type-0.2", "src_end": "water_type-0.3", "tail": 0.3, "text":
     "{cam:composer}Now, a totally different user: an AI company planning a fifty megawatt data centre near {src:dc_send}Gwal Pahari. "
     "{cam:full}{src:dc_send+1.5}ReRoot checks water stress, NASA groundwater data and the local {src:dc_done}climate. "
     "{cam:dcrisk}{hl:dcrisk}{src:dc_done+3.9}Verdict: extremely high water stress. {hl:dcbars}Evaporative cooling would need nine "
     "hundred and forty four million litres a year! {cam:dcloop}{hl:dcflows}{src:dc_loopview_reveal+1.4}So it designs a loop: treated "
     "wastewater covers seventy percent of the cooling, solar is right next door, and {cam:full}{hl:off}a hundred and twenty percent "
     "of the freshwater gets replenished."},

    {"kind": "screen", "chapter": 4, "group": "lakes", "src_start": "water_type-0.2", "src_end": "tl_type-0.3", "text":
     "{cam:composer}But are the lakes they fund {src:water_send}actually holding water? {cam:bars}{src:water_done+0.6}The replenishment "
     "ledger {hl:bars}says this portfolio claims a hundred and twenty five percent of its target, but from space, only thirty five "
     "percent checks out. {cam:full}{hl:off}{src:water_badkhal-0.6}Look at {src:water_badkhal+0.2}Badkhal Lake. "
     "{cam:chart}{hl:chart}{src:water_detail_reveal+1.6}Sentinel-1 radar measures its water every month, right through monsoon "
     "clouds, {cam:table}{hl:season}{src:water_table_view_reveal+1.4}with seasons from the lake's own rainfall. {hl:table}Water came "
     "back after the 2023 works, but now most of it is dense green. So it's not verified. It's at risk."},
    {"kind": "screen", "group": "lakes", "src_start": "tl_type-0.2", "src_end": None, "tail": 0.7, "text":
     "{cam:composer}Want to see it with your own {src:tl_send}eyes? {cam:tl}{src:tl_shown+0.2}Eight years of Sentinel-2: the dry bed, "
     "the revival, then the green takeover. Ready to hand to an auditor."},

    {"kind": "gfx", "draw": "outro", "tail": 2.2, "text":
     "That's the full loop, {g:fill}from planting to proof. And it all runs on Google Cloud: {g:t1}Earth Engine, {g:t2}Gemini on "
     "Vertex AI, {g:t3}BigQuery, {g:t4}Cloud Run, {g:t5}Pub/Sub and {g:t6}Text to Speech. {g:logo}Because planting isn't the finish "
     "line. Proof is. {g:url}Try it now at reroot earth dot com."},
]

# Camera regions (CSS px of the 1440x810 recording). name = a recorded element box; rect = literal.
ROIS = {
    "full": "full",
    "roles": {"name": "roles", "pad": 40},
    "chat": {"rect": [0, 250, 440, 400], "min_w": 900},
    "sites": {"rect": [1030, 56, 410, 230], "min_w": 900},
    "site1": {"name": "dev_site1_detail", "pad": 40},
    "composer": {"rect": [0, 560, 440, 250], "min_w": 900},
    "kpis": {"name": "plan_kpis", "pad": 70},
    "phone": {"rect": [700, 250, 380, 470], "min_w": 900},
    "dcrisk": {"union": ["dc_risk", "dc_bars"], "pad": 60},
    "dcloop": {"union": ["dc_loop", "dc_flows"], "pad": 50},
    "bars": {"name": "water_bars", "pad": 60},
    "chart": {"name": "water_chart", "pad": 50},
    "table": {"union": ["water_season2", "water_table2"], "pad": 40},
    "tl": {"name": "tl_card", "pad": 30},
}

HLS = {
    "roles": {"box": "roles", "label": "Pick who you are", "color": "green"},
    "steps": {"box": {"name": "dev_steps", "live": True}, "until": "dev_done", "label": "Gemini plans, then calls tools", "color": "green", "dim": False},
    "sites": {"box": {"rect": [1050, 70, 362, 118]}, "label": "11 sites · 115 ha", "color": "green"},
    "site1": {"box": "dev_site1_detail", "label": "Why this site", "color": "green"},
    "kpis": {"box": "plan_kpis", "label": "Ready for forest clearance", "color": "green"},
    "monsteps": {"box": {"name": "mon_steps", "live": True}, "until": "mon_done", "label": "Satellite + weather checks", "color": "green", "dim": False},
    "monlist": {"box": "mon_list", "label": "Health vs expected", "color": "amber"},
    "phone": {"box": "mon_phone", "label": "Caretaker's phone", "color": "green"},
    "dcrisk": {"box": "dc_risk", "label": "Water stress + groundwater", "color": "red"},
    "dcbars": {"box": {"rect": [1063, 300, 335, 110]}, "label": "Cooling water a year", "color": "blue"},
    "dcflows": {"box": "dc_flows", "label": "The resource loop", "color": "blue"},
    "bars": {"box": "water_bars", "label": "Claimed vs verified", "color": "blue"},
    "chart": {"box": "water_chart", "label": "Monthly water from radar", "color": "blue"},
    "season": {"box": "water_season2", "label": "Its own seasons", "color": "blue"},
    "table": {"box": "water_table2", "label": "Before vs after, rain adjusted", "color": "amber"},
}
