# Naija Solar: a solar advisor you can talk to, in your own language

*Built for the Build Small Hackathon, hosted by Gradio and Hugging Face.*

**Try it live: [huggingface.co/spaces/build-small-hackathon/naija-solar](https://huggingface.co/spaces/build-small-hackathon/naija-solar) &nbsp;·&nbsp; Watch the demo: [youtu.be/PfQeRfNof8Y](https://youtu.be/PfQeRfNof8Y)**

[![Watch the Naija Solar demo on YouTube](https://img.youtube.com/vi/PfQeRfNof8Y/hqdefault.jpg)](https://youtu.be/PfQeRfNof8Y)

In Nigeria, electricity is something you plan your day around. The grid arrives for a few hours if you are lucky, and the rest of the time the streets fill with the sound of petrol generators. Those generators are expensive, and they are everywhere. The country spends around fourteen billion dollars a year keeping them running, and roughly ninety million people still have no reliable power at all. That is the largest electricity gap of any country on earth.

Solar is the obvious way out. The sun is free, panels have never been cheaper, and a good system pays for itself over time. So why does almost no one I know actually make the switch?

The honest answer is that solar is never explained in a way that ordinary people can act on. Every time I watched a neighbour or a shop owner try to buy a system, they hit the same wall. How many panels do I need? What size of inverter? Which battery, and what will the whole thing really cost? The answers live in spreadsheets and vendor WhatsApp groups, written in English, full of words like kilovolt-amperes.

That wall is taller than it looks. More than a third of Nigerian adults cannot comfortably read a quote or a spec sheet. Fluent English belongs mostly to the cities and to people who finished school, while tens of millions live their whole lives in Hausa, Yoruba, Igbo, or Pidgin. We have over five hundred languages here, and solar is explained in almost none of them. So the people who would gain the most from leaving the generator behind are the very people the industry forgets to talk to.

I wanted to build the opposite of that wall. Something you can simply talk to. Here is exactly how it works.

## Step one: tell it what you run, in your language

![Naija Solar home screen: choose your language, then say or type your appliances](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/01_home.png)

You begin by choosing your language: English, Nigerian Pidgin, Yoruba, Hausa, or Igbo. The whole interface follows your choice, including the explanation you get at the end. Then you tell the app what you run at home. You can speak it, type it, tap a ready-made example, or even take a photo of your room and let it read the appliances for you.

![Four ways to tell it: speak, type, tap an example, or snap a photo](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/02_input.png)

Something as plain as "I get one fridge, two fans, and six bulbs" is all it needs to start. It is forgiving too, so "frige" and "aircondtioner" still land on the right appliance. And when it reads a photo, it shows you exactly what it spotted, so you can confirm or fix it before going on.

## Step two: it sizes your system

![The sized result: daily energy, inverter size, total cost, and the exact recommended panels, inverter, and battery](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/03_result.png)

Behind that simple question, the app does the careful work a good installer would do, only faster. It adds up how much energy you use in a day, works out your peak load and the extra surge when something like a fridge or a pump first switches on, and then chooses the right number of panels, the right inverter, and the right battery to carry you through the night. It prices the whole system against a real catalogue of Nigerian panels, inverters, and batteries, so the figure on the screen sits close to the figure in the market. Importantly, none of these numbers are guessed by a language model. The sizing is plain arithmetic you could check by hand. The model's only job is to understand you and to explain the result.

## Step three: see your home, in 2D and 3D

![A clear 2D diagram of the home: sun, roof panels, your appliances, and the inverter and battery](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/04_view2d.png)

A clean, labelled diagram lays out your appliances, the panels on your roof, and the inverter and battery, so the whole system makes sense at a single glance. A second view puts the sun against your daily usage on a 24 hour chart, which finally makes the battery obvious. The sun is generous at noon, when the house is empty, and shy in the evening, when everyone is home.

![The same home rendered in 3D, with the recommended panels on the roof and the appliances inside](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/05_home3d.png)

And for the part that makes people smile, the app builds your living room in 3D, with the exact recommended panels on the roof, your appliances inside, and the inverter and battery on the wall.

## Step four: it writes your plan, then reads it aloud

This is the part I care about most. The moment your result is ready, the plan appears in plain words on the screen, and then the very same words are read aloud in the language you chose. You can read it, listen, or both. Putting the writing first matters more than it sounds. A voice has to warm up and speak, which takes a few seconds, but the words are there instantly, so you are never left watching a spinner waiting to be told what to buy.

![The plan in writing, then read aloud in your language](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/06_narration.png)

For Yoruba, Hausa, and Igbo, the spoken plan is phrased the way a person actually talks, with the counts read as proper number words instead of bare digits, while the exact figures stay on the cards above. So the voice sounds native and natural, and what you read is still what you hear.

Then it waits for your questions. You can ask why it picked those panels, whether the system can run your air conditioner at night, or how it compares to your generator, and it answers in plain words, grounded in the plan it just built for you.

## The small things that make it feel like an app

A few touches turn this from a demo into something you would actually keep open. The first time you arrive, a short guided tour points out how to speak, type, or photograph your appliances, in your own language. When you are done, you can size another system in a single tap. There is a light mode and a dark mode, and the app remembers which you prefer. You can make an account to save your sizings and open them again later. And the front page carries real ratings from people who have used it, each shown with the language they sized in, so a Hausa speaker can see another Hausa speaker vouch for it.

![A guided first run, in dark mode](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/07_dark.png)

## Under the hood: how it all fits together

If you are curious about what happens between your words and your plan, here is the whole journey on a single page.

![How a request flows through Naija Solar, in five steps](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/flow.png)

The most important decision is hiding in plain sight. The part you cannot afford to get wrong, the actual maths, never touches a language model. A small, deterministic engine does the load profile, the panels, the inverter, the battery, and the cost, all in plain Python over a real catalogue of Nigerian prices. The models are kept to the jobs they are genuinely good at, which is hearing you, reading a photo, and putting the answer into warm, human words.

![The Naija Solar architecture: a free CPU front end calling small open models hosted on Modal](https://huggingface.co/spaces/build-small-hackathon/naija-solar/resolve/main/assets/arch.png)

The front end is an ordinary Hugging Face Space on a free CPU box. It holds the interface, the parser, the sizing engine, and the price catalogue, and it draws the 2D and 3D views right there on your device. Whenever it needs to hear, see, or speak, it calls a handful of small models I host on Modal, where each one wakes up on demand and goes back to sleep when no one is using it, so there is no GPU bill quietly running in the background. A persistent cache keeps the voice clips it has already made, so a plan it has spoken once plays back instantly. A small private dataset keeps the usage count, the feedback, and any emails people choose to leave.

That whole shape is what lets the next part be true.

## Why everything runs on small models

There is a quiet rule behind the whole project. Every model Naija Solar uses is under four billion parameters.

This is not a stunt. It is the point. An app meant for everyday Nigerians has to be cheap to run, or it will never survive past a demo. So the language work runs on Qwen3 at 1.7 billion parameters, speech recognition runs on a small Whisper model, the voice comes from a three billion parameter model I fine-tuned myself (more on that below), and reading appliances from a photo runs on MiniCPM-V, OpenBMB's small open vision model. The largest single piece is still under four billion, and the whole stack lives on Modal, where each part wakes up when it is needed and goes back to sleep when it is not. There is no frontier model behind the curtain and no per word billing to worry about. Small, open models can carry a real product if you give them the right jobs.

To keep the five languages dependable, the explanation for Pidgin, Yoruba, Hausa, and Igbo comes from carefully written templates rather than the small model, because a 1.7 billion parameter model is not reliable at those languages yet. The words you read on screen are exactly the words you hear, so nothing drifts between the two. That single choice is what makes the promise hold: pick Yoruba, and you really do get Yoruba.

## A voice of its own

There was one piece I could not buy off the shelf. The words were handled, but who would speak them? For Nigerian languages the honest options were a robotic voice or no voice at all. Orpheus, one of the most natural open speech models there is, had never learned a single Nigerian language. So I taught it. Starting from Hypa-Orpheus, which already understood Yoruba, Hausa, and Igbo, I fine-tuned a small adapter over 31,574 audio clips drawn from NaijaVoices, WAXAL, FLEURS, BibleTTS, and the one clean Nigerian Pidgin corpus that exists, and I added the language none of those base voices could speak before, Pidgin itself. The entire training run, from streaming and encoding the audio to pushing the finished weights, is a single serverless job on Modal.

Teaching it to speak was only half the work. A solar plan is full of numbers and units, and a Yoruba voice reading "one point five kVA" in English mid-sentence sounds wrong, however good the voice is. So the spoken plan for each Nigerian language is phrased for the ear, with the counts as native number words and the hardest units left to the written cards. The result is a voice that is both native and natural. It lives openly at [Shinzmann/sorotts](https://huggingface.co/Shinzmann/sorotts), it is still smaller than four billion parameters, and it is what reads your plan back to you now. You can hear it speak all four languages on the model page.

## Where it goes next

Right now the app sizes a system, writes it out, and speaks it back. Accounts and saved history are already in place, alongside a live count of systems sized, ratings from real users, and an optional email list, because the goal was never a single demo. Next I want it to remember a household over time, follow real generation, and become the thing a vendor opens with a customer rather than a barrier between them. The goal is the shop owner in Aba who hears, in Igbo, exactly what it takes to keep her freezer cold and her lights on.

If you have ever stared at a solar quote and felt locked out, this one is for you.

**Try it yourself: [huggingface.co/spaces/build-small-hackathon/naija-solar](https://huggingface.co/spaces/build-small-hackathon/naija-solar). Speak your appliances, and watch your home light up.**


By: Emmanuel Ashinze
