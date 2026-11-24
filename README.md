# pitchmap 🍌

**Find local businesses on Google Maps, find one real reason to write to each, and send one honest email at the right local time. Free and open source, made by [Somosbanana](https://somosbanana.com).**

A [Claude](https://claude.com/claude-code) skill + Python scripts + 4 n8n workflows:

```
Google Maps (official Places API) → check each website → 1 real reason → 1 short email
→ n8n queue → sent at 05:30 their time → follow-up after 3 days → replies sorted → your CRM page
```

Two modes:

| mode | for | the reason to write |
|---|---|---|
| `fix` | web designers, SEO, booking systems, anyone selling the fix for a website problem | old copyright year, no HTTPS, not mobile-ready, no online booking, no tap-to-call… |
| `offer` | anyone selling a product or service to businesses | your own patterns on their site ("now hiring", no booking, no shop…), plus their trade and city |

Optional: read their worst recent Google reviews and quote a complaint your offer fixes ("nobody answered the phone").

## Quick start

1. Install the skill: copy this folder to `~/.claude/skills/pitchmap` (Claude Code), or upload it as a skill in Claude.
2. Ask Claude: *"Use pitchmap to find me clients. I sell …"*. Claude asks a few questions and writes `config.json` for you.
3. Or by hand:

```bash
cp config.example.json config.json            # fill it in
python3 scripts/pitchmap.py test              # self-check
export GOOGLE_PLACES_KEY=...                  # Places API (New), key restricted to it
python3 scripts/pitchmap.py find batch1       # businesses from Google Maps
python3 scripts/pitchmap.py profile batch1    # check websites, find emails → data/batch1.leads.csv
python3 scripts/pitchmap.py emails batch1     # write emails → read data/batch1.sample.md
python3 scripts/n8n_build.py                  # n8n workflows → import them (see SKILL.md, step 5)
python3 scripts/pitchmap.py load batch1       # queue them in n8n
```

Needs Python 3.9+ (no packages), a Google Cloud key for the Places API, and n8n (self-hosted or cloud). Replies are sorted by any OpenAI-compatible model; a small local Ollama model is enough and free.

## Play fair

One real observation per email or no email. Business addresses only, your real name and postal address, an easy opt-out, low daily volume from a warmed-up domain. Cold email to businesses is workable in the US, UK, Australia and New Zealand; Canada is stricter, and in Spain and much of the EU it's restricted even B2B. Details in [SKILL.md](SKILL.md).

---

### En español

**pitchmap** busca negocios en Google Maps con la API oficial, revisa su web para encontrar un motivo real para escribirles y redacta un email corto y honesto para cada uno. Después lo pone en cola en n8n para enviarlo a las 5:30 de su hora local, con seguimiento a los 3 días, respuestas clasificadas y una página de CRM.

Tiene dos modos: **fix**, si vendes el arreglo de algo que se ve en su web (diseño web, SEO, reservas), y **offer**, si vendes cualquier producto o servicio a empresas. Instálalo como skill y dile a Claude: *«Usa pitchmap para buscarme clientes. Vendo…»*.

Los emails salen en inglés para EE. UU., Reino Unido, Canadá, Australia y Nueva Zelanda. En España y buena parte de la UE el email comercial no solicitado está restringido incluso entre empresas.

MIT License · © Somosbanana
