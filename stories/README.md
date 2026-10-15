# Writing a story

A story is a folder under `stories/` with a `story.json` and its reference image. Run one with `--story stories/<folder>` (the default is `stories/lego-void`).

`story.json` is a **blueprint, not a script**. It holds what stays true whatever the viewer does: the situation, the question the film keeps open, what keeps happening, the storyteller's voice, the cast and the look. Everything that happens in time (scenes, beats, lines, the arc) is written live by the Showrunner and the Director while the viewer steers. There is deliberately no place for acts, a scene list or "if the viewer does X": an authored sequence is something the film would keep pulling back to, away from the viewer.

The film's two writers read the blueprint:

- **Director**: writes every beat, clip by clip, and answers the viewer. It is the only writer on the critical path.
- **Showrunner**: works on a slower clock. It keeps the story's memory and writes the arc and the scene card for the stretches when the viewer is quiet.

The **compiler** turns each clip into the video model's prompt; the **validator** holds the Director to the cast and perspective rules.

Every text field is optional unless marked *required*. An empty field adds no text to any prompt, so a small story stays small. Keep prose fields to one to three sentences and lists to about five items: the models follow what is short.

## What the film is

**`title`** *(required)*. The film's name. Both writers see it; the transcript, the timing log and the web player show it.

**`premise`** *(required)*. The situation in one or two sentences: who, where, what is going on. Both writers read it first; everything else is measured against it.
> "A LEGO minifigure alone in an infinite white space does whatever comes to mind…"

**`question`**. The dramatic question the film keeps open, the spine of the arc: "Does Mara leave the light before dawn?" The Showrunner keeps it open and moves it scene by scene, never answering it before the film has earned the answer; the Director sees it so each beat can touch it. If you know the ending, write that as a truth, not here.

**`engine`**. What keeps happening: the repeatable pattern that produces scenes, like a TV series' story engine or the game of a comedy scene. The Showrunner draws its scene cards from it; the Director uses it to heighten. Without a question or an engine the film tends to wander, so give it at least one.
> "A prop arrives, he finds a bit to do with it, the bit escalates until it breaks, and he tries again."

**`opening`**. Where the film starts: the routine before anything tilts it. Only the Showrunner reads it, when it writes the first scene card before anything is on air.

**`truths`**. What holds whatever the viewer does: the world's physics, facts, the past, anything fixed about the end, how this world works morally. Both writers treat them as absolute. A viewer request that would break one is bent toward its spirit, not refused, and the Director says so in its reason. Rules that are about the picture belong here too when the Director must obey them while writing (Lego: "Every minifigure's face is one fixed print. Never write a facial expression…").

## Who tells it: `storyteller`

What makes the film the author's when the viewer steers. Both writers read all four.

**`theme`**. What the story is about: the value at stake ("what people sacrifice for duty"). The Showrunner decides what each scene is *about* from it.

**`tone`**. The register of the whole film: mood, humour, how people talk. It shapes every action, line and scene.
> "Earnest, warm physical comedy… When someone speaks it is plain and brief."

**`temperament`**. How this storyteller takes the unexpected and paces the film. The best temperaments translate viewer impulses into the story's currency ("When the viewer wants spectacle, give them beauty"). Rhythm goes here too: how much quiet, how long setups run before they pay off. The Director absorbs viewer lines through it; the Showrunner uses it to judge how long threads run.

**`never`**. What this storyteller would never do, one item per refusal, each written to complete "This storyteller would never: …" ("Turn cynical, or wink at the audience."). Refusals are the clearest signature a story has.

## Where it happens: `locations`

A list of places, each with a **`name`** and a **`description`**. Both writers see the list. The film starts in the first one. The Director moves characters and props between them by id (the name in lower case with underscores: "The White Void" → `the_white_void`). No story with two locations has run yet; the location resolver has a known gap (see `docs/ARCHITECTURE.md`).

## Who is in it: `cast`

**`protagonist`**. The name of the character the film follows (empty: the first character). The validator keeps the protagonist single and in the story (never removed, never doubled), and both writers are told to hold its look in every clip.

**`newcomers`**. What kind of characters the film may introduce beyond the cast, with any guidance on them ("Other minifigures from any LEGO theme… with the same fixed printed face"). Empty means none: the validator refuses a new character, and anything else that appears is a prop. A newcomer is registered once with its look, who it is and what it wants, its voice and its state, and stays the same in every later clip.

**`characters`**. A list. Each character's id is its name in lower case with underscores ("Lego Man" → `lego_man`). Both writers see each character's cast sheet.

- **`name`** *(required)*. How every prompt names the character.
- **`who`**. Who the character is in the story, one sentence. The cast sheet's first line.
- **`wants`**. What the character wants. It colours what the character does and how it takes a viewer's suggestion: willingly, its own way, or with resistance.
- **`nature`**. How the character is: temperament, habits, the flaw. Shapes how the character acts and talks.
- **`voice`**. How the character sounds, which keeps one voice from clip to clip. Either a plain string, read as the description, or an object:
  - `description`: a few words ("crisp, clipped, with a faint radio crackle");
  - `language`: the language its lines are written and spoken in;
  - `accent`;
  - `delivery`: a default way of speaking.

  Any character may speak. A line is compiled as `Name says in a {delivery, description} voice, in {language} with {accent}, "line"`.
- **`look`**. The full look, verbatim for the video model: on the film's first clip, and whenever the character enters the picture. Write what the camera sees; never a facial expression.
- **`look_short`**. The look on every other clip the character is in. Keep the words that hold identity: in GPU tests, shorter looks drifted within twelve clips; "bare yellow head" and "plain red torso" are what keep hair pieces and printed shirts away. Empty: the name only.
- **`state`**. The starting state in a few words: the body, the place in the frame and what the character is doing ("standing upright centre frame, arms at his sides"). For the protagonist it is where the first clip starts. The world model then tracks it clip by clip, as prose the Director rewrites whole when it changes. (Older blueprints split it into `posture`, `activity` and `position`; they are still read and joined.)
- **`appears`**, **`appears_when`**. For supporting characters: whether they `must`, `should` or `may` (the default) enter, and the narrative condition for it ("the first time one of Lego Man's bits fails badly"). Only the protagonist is in the film at the start. The Showrunner decides when an entrance serves the story (it puts it on a new scene card) and the Director stages it. Ignored for the protagonist.

## How it looks: `picture`

**`style`**. The full visual style. It opens the prompt of the film's first clip and is the Director's "Visual world". Describe light, background, colour and motion; the video model paints what is written.

**`anchor`**. One sentence of look and framing that opens every later clip's prompt, so the style holds over long films without the full text (empty: the style's first sentence).

**`reference`**. The image the film's first clip is rendered from, and what it shows:
- `image`: the file, inside the story folder. The live config takes only an image at the render aspect to the pixel (16:9) and refuses anything else at startup.
- `shows`: what the image shows in words, since the Director cannot see it. The first clip opens with it ("The shot opens with {shows}, then the action begins"), and the Director is told to begin there. Name any character in the image by its cast name (case does not matter): a character `shows` names is already in the picture; any other character on screen in the first clip enters it. `shows` may be blank, and the image may show no character at all (a landscape): the protagonist then walks into the first clip.

A story may leave out `reference` entirely. It runs headless, but a video run refuses to start without one, since the renderer starts every film from an image.

**`perspective`**. How the film is shot:
- `default`: "third-person" unless you say otherwise.
- `allowed`: other perspectives a beat may switch to (["first-person"]). The validator refuses any perspective not listed, and the compiler shoots a first-person beat as a point-of-view shot.
- `switching`: when a switch is right ("Only while a minifigure looks through something…").
- `cuts`: whether a clip may cut to a new shot. Without it the film is one continuous take: a change of angle is a camera move, and the validator drops any cut. The Lego story is one take, because cuts repainted faces on the GPU.
- `cutting`: the story's guidance on when to cut, when cuts are on.

## Checking a story

`Story.from_json` loads and checks the blueprint: a protagonist that is one of the characters, a reference image that exists and decodes. A headless run (`python main.py play --config configs/director.yaml`) shows what the writers make of it: the transcript under `logs/` has every clip, its lines, its compiled video prompt and the world's state (the writers' own prompts are in Langfuse when tracing is on). `stories/lego-void/story.json` uses every field.
