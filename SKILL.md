---
name: pitchmap
description: Find local businesses on Google Maps, check their websites for a real reason to write, draft one short relevant cold email each, and queue them in n8n to send at the right local time with follow-ups, reply sorting and a CRM page. Two modes - "fix" (you sell the fix for a problem their website shows, e.g. web design) and "offer" (you sell any product or service to businesses). Use when the user wants to prospect, find clients or leads, build a lead list from Google Maps, sell B2B to local businesses, or run cold email outreach.
---

# pitchmap

A prospecting pipeline that finds businesses, checks each one for a real reason to get in touch, and writes one honest email per business. It never sends a message without a concrete observation behind it.

```
Google Maps (Places API) -> check website -> 1 real reason -> 1 email -> n8n queue -> sent 05:30 local time -> follow-up -> replies sorted -> CRM
```

All commands run from this skill's folder. `python3 scripts/pitchmap.py test` checks the setup without network.

## 1. Understand what the user sells (always first)

Ask, in one message, only what you can't infer:
- What do they sell, to whom, and roughly at what price? Which countries?
- What would make a business need it *right now*? This becomes the signal.
- Sender name, business name, website, the email address they'll send from, and their real postal address (required in the footer by anti-spam law).

Then pick the mode:
- **fix**: what they sell fixes something visible on the prospect's website (web design, SEO, booking systems, speed, accessibility, chatbots). The built-in website checks are the signals.
- **offer**: anything else sold to businesses (software, supplies, insurance, cleaning, photography, accounting, training). Write signals as patterns on the prospect's site: `missing` (e.g. no online booking -> sell a booking tool) or `present` (e.g. "now hiring" -> sell recruitment). If there's truly no website signal, set `only_with_signal: false` and the email leans on the category + city match instead; say clearly to the user that replies will be lower.

## 2. Write config.json

Copy `config.example.json` to `config.json` and fill it in with the user's answers:
- `search.queries`: one trade in one area per query: `"physiotherapist in Leeds, UK"`. Narrow beats broad. Each query returns up to 60 businesses; split cities into neighbourhoods to get more. 50–150 queries is a good batch.
- `signals[].line`: one plain sentence a business owner would agree with. It's inserted after "One thing I noticed: ". No jargon ("meta description" -> "Google shows a random chunk of text instead of a real pitch").
- `offer.what` completes "I build / I offer ___." `{plural}` = their trade, e.g. "plumbers".
- `offer.cta`: one small ask (a free sample, a 10-minute look). Never "book a call" on email one.
- `offer.prices`: one entry price per country, or remove the field.
- `followups`: 1–2 short bumps. `{hi}`, `{name}`, `{sender}` are filled in.
- `replies`: drafts for interested / price questions. Classifier: any OpenAI-compatible endpoint (local Ollama by default, free).

Show the user one sample email (step 4) before going further.

## 3. Find and profile

```bash
export GOOGLE_PLACES_KEY=...        # Google Cloud: enable "Places API (New)", create a key restricted to it
python3 scripts/pitchmap.py find batch1
python3 scripts/pitchmap.py profile batch1
```

`find` uses Google's official API (allowed by their terms, with a monthly free allowance; never scrape Maps pages). `profile` opens each website, applies the signals, finds a public business email, works out city and time zone, and skips anyone already contacted in an earlier batch. Expect roughly 1 usable lead per 5–10 businesses found.

`search.reviews: true` also reads up to 5 Google reviews per business and quotes a recent complaint the user's offer fixes ("nobody answered the phone"). It costs more per request: tell the user before turning it on.

## 4. Write the emails and review them

```bash
python3 scripts/pitchmap.py emails batch1
```

Open `data/batch1.sample.md` and read it with the user. Check: the observation is true and specific, under ~120 words, one small ask, real footer with an opt-out. Fix `config.json` and re-run until it reads like a person wrote it. The same lead always gets the same wording, so re-running is safe.

## 5. n8n (once)

```bash
python3 scripts/n8n_build.py        # -> n8n/intake.json, send.json, replies.json, crm.json
```

In n8n:
1. Data tables -> create `pitchmap_leads` (or the name in `n8n.data_table`) with these string columns: `email name city country tz web problem sector subject body sent_at followup_at reply reply_at summary` and a number column `n`.
2. Import the 4 files. Open the red nodes and pick the SMTP, IMAP and Telegram credentials for the sending mailbox.
3. Publish `intake` and `crm` first, then set `n8n.intake_url` in config.json to the intake webhook's production URL.
4. CRM page: `https://<your-n8n>/webhook/crm?k=<n8n.secret>`.

## 6. Load and go

```bash
python3 scripts/pitchmap.py load batch1
```

Then publish `send` and `replies`. Emails go out on weekdays at `sending.local_time` in each business's time zone, two per minute per zone, within the daily caps; a follow-up goes 3 days later to anyone who hasn't replied. Opt-outs and plain "no thanks" get an automatic polite reply and are never emailed again; everything else reaches the user on Telegram with a suggested reply.

## Rules (tell the user; don't skip them)

- One real observation per email, or no email. Never invent a problem, a relationship or a result.
- Every email: the sender's real name and business, a postal address, and an easy opt-out. Honour opt-outs at once.
- Business addresses only (info@, office@, the owner's work email listed on their own site). Never buy lists, never guess personal emails.
- Law varies: B2B cold email is workable in the US, UK, Australia and New Zealand with the above; Canada (CASL) only when the address is published and the message is relevant to their role; in Spain and much of the EU unsolicited commercial email is restricted even between businesses, so don't run it there without legal advice.
- Deliverability: send from a dedicated domain or mailbox with SPF, DKIM and DMARC set up, warm it up (start at 10–20 a day), and keep the caps. Never send from a client's main domain on day one.
- Start with 1 trade and 1–2 countries, read every reply for the first week, then scale.
