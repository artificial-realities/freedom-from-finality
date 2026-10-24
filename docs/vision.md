# The vision

What Untitled Project is reaching for, and the problems in the way. The [README](../README.md) says what the software does today; [ARCHITECTURE.md](ARCHITECTURE.md) how it does it.

The experience should feel like watching a continuously unfolding movie guided by an imaginative, responsive storyteller, much like listening to someone tell a story around a campfire. The story should have a life of its own, progressing naturally and remaining engaging even when the viewer does nothing. Characters should have distinct personalities, motivations, relationships, and autonomy, while the world maintains its own internal logic and consistency.

The viewer should be able to influence the story at any moment through natural, unrestricted input, as if making suggestions to the storyteller rather than issuing commands or selecting from predefined choices. These suggestions should be interpreted creatively and integrated organically into the unfolding narrative, without automatically being obeyed or disrupting its coherence. Viewer input should have the potential to create meaningful consequences, both immediate and long-term, while characters remain free to act according to their motivations. The storyteller should balance responsiveness with creative independence, introducing believable surprises, developing conflicts, and maintaining narrative momentum without relying on the viewer to drive every event.

The experience should feel cinematic, immersive, and continuous, with natural transitions, consistent environments, believable dialogue, and coherent visual and audio progression rather than a sequence of disconnected generated clips. Above all, it should feel like a living story that listens to the viewer, remembers what happens, and evolves naturally in ways that neither the viewer nor the storyteller has completely predetermined.

The goal is not a clip generator with a text box on it. It is a system that can hold a story together across a long runtime while remaining open to influence.

## The problem

Getting there means solving several things at once:

- **Continuous generation** — produce and present video without the stream stopping to think.
- **Continuity over time** — keep story, characters, world, and narrative thread coherent as the runtime grows.
- **Influence** — accept user input and interpret it as story intent rather than as a raw prompt.
- **Translation** — turn that intent into concrete changes in future scenes.
- **Constraints** — preserve higher-level story guidelines while still letting influence matter. Steering should bend the story, not break it.
- **Cinematic cohesion** — connect scenes so the result reads as one film, not a reel of isolated AI clips.

None of these is fully solved here. The system is one attempt at attacking them together.
