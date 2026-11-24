#!/usr/bin/env python3
"""Builds the 4 n8n workflows from config.json into n8n/:

  intake.json   POST /webhook/pitchmap-intake  -> saves queued emails in the data table (pitchmap.py load uses it)
  send.json     every minute: sends at local_time in each lead's time zone, weekdays only, with daily caps + 1 follow-up
  replies.json  reads your inbox, classifies replies, alerts you on Telegram, handles opt-outs, updates the table
  crm.json      GET /webhook/crm?k=<secret> -> a simple CRM page with every lead and its status

Import: n8n -> Workflows -> Import from file. Then pick your SMTP / IMAP / Telegram credentials in the red nodes and Publish.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
C = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
B, S, R, N = C["business"], C["sending"], C["replies"], C["n8n"]
DT = {"__rl": True, "mode": "name", "value": N["data_table"]}
SMTP = {"smtp": {"id": "", "name": "SMTP (choose yours)"}}
IMAP = {"imap": {"id": "", "name": "IMAP (choose yours)"}}
TG = {"telegramApi": {"id": "", "name": "Telegram (choose yours)"}}
js = json.dumps  # safe way to put config values inside JavaScript


def node(id, name, type, ver, pos, params, **extra):
    return {"id": id, "name": name, "type": type, "typeVersion": ver, "position": pos, "parameters": params, **extra}


def link(*pairs):
    c = {}
    for a, b, *out in pairs:
        o = out[0] if out else 0
        c.setdefault(a, {"main": []})
        while len(c[a]["main"]) <= o:
            c[a]["main"].append([])
        c[a]["main"][o].append({"node": b, "type": "main", "index": 0})
    return c


def where(key, cond, value=None):
    c = {"keyName": key, "condition": cond, **({"keyValue": value} if value is not None else {})}
    return {"matchType": "allConditions", "filters": {"conditions": [c]}}


def IF(id, expr):
    return {"conditions": {"options": {"caseSensitive": True, "typeValidation": "loose", "version": 2}, "combinator": "and",
                           "conditions": [{"id": id, "leftValue": expr, "rightValue": True, "operator": {"type": "boolean", "operation": "true", "singleValue": True}}]}, "options": {}}


HTML_JS = f"""const SIGNATURE = {js(B.get('signature_html', ''))};
const esc = s => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
const para = s => s.split(/\\n{{2,}}/).map(x => `<p style="margin:0 0 14px">${{esc(x).replace(/\\n/g,'<br>')}}</p>`).join('');
// Plain text -> HTML: body, optional HTML signature, quoted first email, grey legal footer
function toHtml(body, quote = '') {{
  const [main, foot = ''] = body.split(/\\n--\\n/);
  return `<div style="font-family:Arial,Helvetica,sans-serif;font-size:15px;line-height:1.5;color:#222">${{para(main)}}${{SIGNATURE}}${{quote ? `<blockquote style="margin:22px 0 0;padding:0 0 0 12px;border-left:2px solid #ddd;color:#777;font-size:13px">${{para(quote)}}</blockquote>` : ''}}${{foot ? `<p style="margin:22px 0 0;font-size:12px;color:#888">${{esc(foot.trim()).replace(/\\n/g,'<br>')}}</p>` : ''}}</div>`;
}}
"""

# ---------- intake ----------
INTAKE = f"""if (($json.headers || {{}})['x-pitchmap-key'] !== {js(N['secret'])}) throw new Error('wrong key');
const seen = new Set($('Existing').all().map(i => String(i.json.email || '').toLowerCase()));
const cols = ['n','email','name','city','country','tz','web','problem','sector','subject','body'];
return ($('Webhook').first().json.body.rows || []).filter(r => r.email && !seen.has(r.email.toLowerCase()))
  .map(r => ({{ json: Object.fromEntries(cols.map(k => [k, r[k] ?? ''])) }}));"""
intake = {
    "name": "pitchmap · intake", "settings": {"executionOrder": "v1"},
    "nodes": [
        node("i1", "Webhook", "n8n-nodes-base.webhook", 2, [0, 0], {"httpMethod": "POST", "path": "pitchmap-intake", "responseMode": "lastNode", "options": {}},
             webhookId="7a1f0c2e-3b4d-4e5f-8a9b-pitchmap0001"),
        node("i2", "Existing", "n8n-nodes-base.dataTable", 1.1, [220, 0], {"resource": "row", "operation": "get", "dataTableId": DT, "returnAll": True, "options": {}},
             alwaysOutputData=True, executeOnce=True),
        node("i3", "New rows only", "n8n-nodes-base.code", 2, [440, 0], {"jsCode": INTAKE}),
        node("i4", "Save", "n8n-nodes-base.dataTable", 1.1, [660, 0], {"resource": "row", "operation": "insert", "dataTableId": DT,
                                                                      "columns": {"mappingMode": "autoMapInputData", "value": {}}, "options": {}}),
    ],
    "connections": link(("Webhook", "Existing"), ("Existing", "New rows only"), ("New rows only", "Save")),
}

# ---------- send ----------
h, m = map(int, S["local_time"].split(":"))
PICK = f"""const START = {h * 60 + m}, CAP_COUNTRY = {S['daily_cap_per_country']}, CAP_TOTAL = {S['daily_cap_total']}, FOLLOW_DAYS = {S['followup_after_days']};
const PER_MINUTE = 2; // spread over the 30-minute window: max 60 per time zone and day
const local = tz => {{ const o = Object.fromEntries(new Intl.DateTimeFormat('en-GB', {{ timeZone: tz, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }}).formatToParts(new Date()).map(x => [x.type, x.value])); return {{ day: o.weekday, min: +o.hour * 60 + +o.minute }}; }};
const all = $input.all().map(i => i.json).filter(r => r.email && r.tz);
const recent = t => t && Date.now() - new Date(t).getTime() < 20 * 3600e3;
const today = all.filter(r => recent(r.sent_at) || recent(r.followup_at));
let left = CAP_TOTAL - today.length;
const perCountry = {{}}; for (const r of today) perCountry[r.country] = (perCountry[r.country] || 0) + 1;
const follow = all.filter(r => r.sent_at && !r.followup_at && !r.reply && Date.now() - new Date(r.sent_at).getTime() >= FOLLOW_DAYS * 86400e3).map(r => ({{ ...r, _follow: true }}));
const fresh = all.filter(r => !r.sent_at).sort((a, b) => a.n - b.n);
const byTz = {{}}; for (const r of [...follow, ...fresh]) (byTz[r.tz] ||= []).push(r);
const out = [];
for (const [tz, rows] of Object.entries(byTz)) {{
  const l = local(tz);
  if (['Sat', 'Sun'].includes(l.day) || l.min < START || l.min > START + 29) continue;
  let k = 0;
  for (const r of rows) {{
    if (k >= PER_MINUTE || left <= 0) break;
    if ((perCountry[r.country] || 0) >= CAP_COUNTRY) continue;
    out.push({{ json: r }}); left--; k++; perCountry[r.country] = (perCountry[r.country] || 0) + 1;
  }}
}}
return out;"""
FOLLOW = HTML_JS + f"""const FOLLOWUPS = {js(C['followups'])}, SENDER = {js(B['sender_name'])};
const r = $json;
if (!r._follow) return {{ json: {{ ...r, html: toHtml(r.body) }} }};
const [main, foot = ''] = r.body.split(/\\n--\\n/);
const hi = (main.split('\\n')[0] || 'Hi there,').trim();
const text = FOLLOWUPS[(r.n || 0) % FOLLOWUPS.length].replaceAll('{{hi}}', hi).replaceAll('{{name}}', r.name || 'your business').replaceAll('{{sender}}', SENDER);
const tail = foot ? `\\n\\n--\\n${{foot.trim()}}` : '';
return {{ json: {{ ...r, subject: /^re:/i.test(r.subject) ? r.subject : 'Re: ' + r.subject,
  body: text + '\\n\\n' + main.trim().split('\\n').map(l => '> ' + l).join('\\n') + tail, html: toHtml(text + tail, main.trim()) }} }};"""
send = {
    "name": "pitchmap · send", "settings": {"executionOrder": "v1", "timezone": "UTC", "saveDataSuccessExecution": "none"},
    "nodes": [
        node("s1", "Every minute", "n8n-nodes-base.scheduleTrigger", 1.2, [0, 0], {"rule": {"interval": [{"field": "cronExpression", "expression": "* * * * *"}]}}),
        node("s2", "Leads", "n8n-nodes-base.dataTable", 1.1, [220, 0], {"resource": "row", "operation": "get", "dataTableId": DT, "returnAll": True, "options": {}},
             alwaysOutputData=True),
        node("s3", "Who's due now", "n8n-nodes-base.code", 2, [440, 0], {"jsCode": PICK}),
        node("s4", "Follow-up text + HTML", "n8n-nodes-base.code", 2, [660, 0], {"mode": "runOnceForEachItem", "jsCode": FOLLOW}),
        node("s5", "Send email", "n8n-nodes-base.emailSend", 2.1, [880, 0],
             {"fromEmail": B["from"], "toEmail": "={{ $json.email }}", "subject": "={{ $json.subject }}", "emailFormat": "both",
              "text": "={{ $json.body }}", "html": "={{ $json.html }}", "options": {"appendAttribution": False}}, credentials=SMTP),
        node("s6", "Mark sent", "n8n-nodes-base.dataTable", 1.1, [1100, 0],
             {"resource": "row", "operation": "update", "dataTableId": DT, **where("id", "eq", "={{ $('Who\\'s due now').item.json.id }}"),
              "columns": {"mappingMode": "defineBelow", "value": {
                  "sent_at": "={{ $('Who\\'s due now').item.json._follow ? $('Who\\'s due now').item.json.sent_at : $now.toISO() }}",
                  "followup_at": "={{ $('Who\\'s due now').item.json._follow ? $now.toISO() : null }}"}}, "options": {}}),
    ],
    "connections": link(("Every minute", "Leads"), ("Leads", "Who's due now"), ("Who's due now", "Follow-up text + HTML"),
                        ("Follow-up text + HTML", "Send email"), ("Send email", "Mark sent")),
}

# ---------- replies ----------
PREPARE = f"""const j = $json;
const raw = j.textPlain || (j.textHtml || '').replace(/<[^>]+>/g, ' ');
const text = raw.split(/\\n\\s*On .+wrote:|\\n-{{2,}}\\s*Original Message|\\nFrom: /i)[0].split('\\n').filter(l => !l.trim().startsWith('>')).join('\\n').trim().slice(0, 2000);
const from = String(j.from || '');
const email = (from.match(/[^<\\s]+@[^>\\s]+/) || [''])[0].toLowerCase();
const name = from.replace(/<.*>/, '').replace(/"/g, '').trim() || email;
const subject = j.subject || '';
const bounce = /mailer-daemon|postmaster|mail delivery/i.test(from) || /undeliver|delivery status|returned mail|delivery fail|failure notice|address not found/i.test(subject);
let to = email;
if (bounce) {{
  const full = String(j.textPlain || '') + ' ' + String(j.textHtml || '');
  const fr = full.match(/(?:Final|Original)-Recipient:\\s*rfc822;\\s*<?([^\\s<>;]+@[^\\s<>;]+)/i);
  to = fr ? fr[1].toLowerCase() : ((full.match(/[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\\.[A-Za-z]{{2,}}/g) || []).map(e => e.toLowerCase()).find(e => !e.endsWith('@' + {js(B['own_domain'])}) && !/mailer-daemon|postmaster/.test(e)) || email);
}}
return {{ json: {{ email: to, name, subject, text, bounce, own: email.endsWith('@' + {js(B['own_domain'])}) }} }};"""
CLASSIFY = ("={{ JSON.stringify({ model: " + js(R["classifier_model"]) + ", temperature: 0, max_tokens: 5, messages: [ { role: 'system', content: "
            + js("Classify a reply to a sales email. Answer with ONE word only:\n"
                 "interested = wants to go ahead, a call, a quote or the free sample\n"
                 "question = asks something (price, timing, how it works) without saying yes\n"
                 "not_interested = says no, not now, already has someone\n"
                 "unsubscribe = asks to stop emails or be removed\n"
                 "auto = out of office, vacation, automatic notice")
            + " }, { role: 'user', content: $('Prepare').item.json.subject + '\\n\\n' + $('Prepare').item.json.text } ] }) }}")
DECIDE = HTML_JS + f"""const REPLIES = {js(R)}, SENDER = {js(B['sender_name'])}, FOOTER = {js(B['name'] + ' · ' + B['postal_address'])}, PRICES = {js(C['offer']['prices'])};
const p = $('Prepare').item.json, lead = $('Find lead').item?.json || {{}};
let cat = String($json.choices?.[0]?.message?.content || $json.message?.content || '').trim().toLowerCase().split(/\\s/)[0].replace(/[^a-z_]/g, '');
if (!['interested','question','not_interested','unsubscribe','auto'].includes(cat)) cat = 'question';
if (p.bounce) cat = 'bounce';
const t = p.text.toLowerCase();
const later = /not (right )?now|maybe later|next (week|month|year)|in a few (weeks|months)|circle back|check back/.test(t);
const stopOnly = /^\\W*(stop|unsubscribe|remove|remove me|no thanks|no thank you|not interested)\\W*$/i.test(p.text);
const optOut = !p.bounce && (stopOnly || (cat === 'unsubscribe' && /unsubscribe|remove|take me off|stop|do not (e-?mail|contact)|don'?t (e-?mail|contact)|opt.?out/.test(t)));
const noThanks = !p.bounce && !optOut && cat === 'not_interested' && !later;
const local = p.email.split('@')[0];
const first = /^[a-z]{{3,10}}$/.test(local) && !/^(info|office|service|sales|admin|contact|support|mail|hello|team|billing)$/.test(local) ? local[0].toUpperCase() + local.slice(1) : (p.name.split(/\\s+/)[0] || '');
const hi = first && !first.includes('@') ? `Hi ${{first}},` : 'Hi there,';
const fill = s => s.replaceAll('{{hi}}', hi).replaceAll('{{sender}}', SENDER).replaceAll('{{name}}', lead.name || 'your business').replaceAll('{{price}}', PRICES[lead.country] || PRICES.default || '');
let draft = '';
if (cat === 'interested') draft = fill(REPLIES.interested);
if (cat === 'question') draft = `${{hi}}\\n\\n${{/price|cost|how much|\\$|£|€|charge|fee/.test(t) ? fill(REPLIES.price) : REPLIES.fallback_question}}\\n\\n${{SENDER}}`;
const reply = optOut ? `${{hi}}\\n\\nDone, I've taken you off my list and you won't hear from me again. Thanks for letting me know.\\n\\n${{SENDER}}\\n--\\n${{FOOTER}}`
  : noThanks ? `${{hi}}\\n\\nThanks for getting back to me, I really appreciate it. I won't email you again. All the best!\\n\\n${{SENDER}}\\n--\\n${{FOOTER}}` : '';
const label = {{ interested: '🟢 INTERESTED', question: '❓ QUESTION', not_interested: '⚪ NOT INTERESTED', unsubscribe: '🔴 OPT-OUT', auto: '🤖 AUTO-REPLY', bounce: '📭 BOUNCED' }}[cat];
const ctx = lead.n ? `\\n📇 #${{lead.n}} · ${{lead.name}} · ${{lead.city}} · ${{lead.web}}` : '\\n📇 not in your list';
const note = optOut ? '\\n\\n✅ Opt-out confirmed automatically.' : noThanks ? '\\n\\n✅ Thanked automatically, never emailed again.' : later ? '\\n\\n⏳ "Not now": answer this one yourself.' : '';
const telegram = `${{label}}\\nFrom: ${{p.name}} <${{p.email}}>${{ctx}}\\nSubject: ${{p.subject}}\\n\\n${{p.text.slice(0, 700)}}${{draft ? '\\n\\n✍️ Suggested reply:\\n\\n' + draft : ''}}${{note}}`;
return {{ json: {{ ...p, cat, leadId: lead.id || null, send_reply: !!reply, replyText: reply, replyHtml: reply ? toHtml(reply) : '', telegram: esc(telegram) }} }};"""
tg_nodes = [node("r7", "Telegram alert", "n8n-nodes-base.telegram", 1.2, [1320, -120],
                 {"chatId": R["telegram_chat_id"], "text": "={{ $json.telegram }}",
                  "additionalFields": {"appendAttribution": False, "disable_web_page_preview": True, "parse_mode": "HTML"}}, credentials=TG)] if R.get("telegram_chat_id") else []
replies = {
    "name": "pitchmap · replies", "settings": {"executionOrder": "v1"},
    "nodes": [
        node("r1", "New email", "n8n-nodes-base.emailReadImap", 2.2, [0, 0], {"mailbox": "INBOX", "postProcessAction": "read", "format": "simple", "options": {}}, credentials=IMAP),
        node("r2", "Prepare", "n8n-nodes-base.code", 2, [220, 0], {"mode": "runOnceForEachItem", "jsCode": PREPARE}),
        node("r3", "Not from me?", "n8n-nodes-base.if", 2.2, [440, 0], IF("c1", "={{ !$json.own }}")),
        node("r4", "Find lead", "n8n-nodes-base.dataTable", 1.1, [660, 0], {"resource": "row", "operation": "get", "dataTableId": DT,
                                                                           **where("email", "eq", "={{ $json.email }}"), "returnAll": False, "limit": 1, "options": {}}, alwaysOutputData=True),
        node("r5", "Classify", "n8n-nodes-base.httpRequest", 4.2, [880, 0],
             {"method": "POST", "url": R["classifier_url"], "sendBody": True, "specifyBody": "json", "jsonBody": CLASSIFY,
              "sendHeaders": bool(R.get("classifier_key")), "headerParameters": {"parameters": [{"name": "Authorization", "value": "Bearer " + R.get("classifier_key", "")}]},
              "options": {"timeout": 120000}}),
        node("r6", "Decide", "n8n-nodes-base.code", 2, [1100, 0], {"mode": "runOnceForEachItem", "jsCode": DECIDE}),
        *tg_nodes,
        node("r8", "Save reply", "n8n-nodes-base.dataTable", 1.1, [1320, 60],
             {"resource": "row", "operation": "update", "dataTableId": DT, **where("email", "eq", "={{ $json.email }}"),
              "columns": {"mappingMode": "defineBelow", "value": {"reply": "={{ $json.cat }}", "reply_at": "={{ $now.toISO() }}",
                                                                   "summary": "={{ $json.text.replace(/\\s+/g, ' ').slice(0, 220) }}"}}, "options": {}}),
        node("r9", "Auto-reply?", "n8n-nodes-base.if", 2.2, [1320, 240], IF("c2", "={{ $json.send_reply }}")),
        node("r10", "Send auto-reply", "n8n-nodes-base.emailSend", 2.1, [1540, 240],
             {"fromEmail": B["from"], "toEmail": "={{ $json.email }}", "subject": "={{ /^re:/i.test($json.subject) ? $json.subject : 'Re: ' + $json.subject }}",
              "emailFormat": "both", "text": "={{ $json.replyText }}", "html": "={{ $json.replyHtml }}", "options": {"appendAttribution": False}}, credentials=SMTP),
    ],
    "connections": link(("New email", "Prepare"), ("Prepare", "Not from me?"), ("Not from me?", "Find lead"), ("Find lead", "Classify"),
                        ("Classify", "Decide"), *([("Decide", "Telegram alert")] if tg_nodes else []), ("Decide", "Save reply"), ("Decide", "Auto-reply?"),
                        ("Auto-reply?", "Send auto-reply")),
}

# ---------- crm ----------
CRM = f"""if ($('GET /crm').first().json.query?.k !== {js(N['secret'])}) return [{{ json: {{ html: 'Not found' }} }}];
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}}[c]));
const d = t => t ? new Date(t).toISOString().slice(0, 16).replace('T', ' ') : '';
const rows = $input.all().map(i => i.json).filter(r => r.email).sort((a, b) => (b.reply_at ? 1 : 0) - (a.reply_at ? 1 : 0) || a.n - b.n);
const state = r => r.reply || (r.followup_at ? 'follow-up sent' : r.sent_at ? 'sent' : 'queued');
const color = {{ interested: '#16a34a', question: '#2563eb', not_interested: '#6b7280', unsubscribe: '#dc2626', bounce: '#b45309' }};
const sent = rows.filter(r => r.sent_at).length, replied = rows.filter(r => r.reply).length, hot = rows.filter(r => ['interested','question'].includes(r.reply)).length;
const tr = rows.map(r => `<tr data-s="${{state(r)}}"><td>${{r.n}}</td><td><b>${{esc(r.name)}}</b><br><a href="https://${{esc(r.web)}}" target="_blank" rel="noopener">${{esc(r.web)}}</a></td><td>${{esc(r.city)}}<br><small>${{esc(r.sector)}}</small></td><td>${{esc(r.email)}}</td><td>${{d(r.sent_at)}}</td><td><span style="background:${{color[r.reply] || '#111'}}">${{esc(state(r))}}</span></td><td>${{esc(r.summary)}}</td></tr>`).join('');
const html = `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>pitchmap CRM</title>
<style>body{{margin:0;font:14px/1.45 system-ui,sans-serif;background:#fafaf7;color:#111}}header{{padding:16px 20px;background:#111;color:#fff;display:flex;gap:24px;flex-wrap:wrap}}header b{{color:#FFD600;font-size:18px}}.w{{padding:16px;overflow-x:auto}}input,select{{padding:8px;border:1px solid #ccc;border-radius:8px;margin:0 8px 12px 0}}table{{border-collapse:collapse;width:100%;background:#fff}}th,td{{padding:8px;border-bottom:1px solid #eee;text-align:left;vertical-align:top}}th{{background:#f3f3ee;font-size:12px;text-transform:uppercase}}td span{{color:#fff;padding:2px 8px;border-radius:99px;font-size:12px;white-space:nowrap}}a,small{{color:#777;font-size:12px}}</style></head>
<body><header><div>Sent <b>${{sent}}</b> / ${{rows.length}}</div><div>Replies <b>${{replied}}</b></div><div>Interested or questions <b>${{hot}}</b></div></header>
<div class="w"><input id="q" placeholder="Search…"><select id="s"><option value="">All states</option>${{[...new Set(rows.map(state))].sort().map(s => `<option>${{s}}</option>`).join('')}}</select>
<table><thead><tr><th>#</th><th>Business</th><th>City</th><th>Email</th><th>Sent</th><th>State</th><th>Their reply</th></tr></thead><tbody>${{tr}}</tbody></table></div>
<script>const f=()=>{{const q=document.getElementById('q').value.toLowerCase(),s=document.getElementById('s').value;for(const r of document.querySelectorAll('tbody tr'))r.hidden=!((!q||r.innerText.toLowerCase().includes(q))&&(!s||r.dataset.s===s))}};document.getElementById('q').oninput=f;document.getElementById('s').oninput=f;</script></body></html>`;
return [{{ json: {{ html }} }}];"""
crm = {
    "name": "pitchmap · CRM page", "settings": {"executionOrder": "v1"},
    "nodes": [
        node("c1", "GET /crm", "n8n-nodes-base.webhook", 2, [0, 0], {"httpMethod": "GET", "path": "crm", "responseMode": "responseNode", "options": {}},
             webhookId="7a1f0c2e-3b4d-4e5f-8a9b-pitchmap0002"),
        node("c2", "Leads", "n8n-nodes-base.dataTable", 1.1, [220, 0], {"resource": "row", "operation": "get", "dataTableId": DT, "returnAll": True, "options": {}},
             alwaysOutputData=True),
        node("c3", "Page", "n8n-nodes-base.code", 2, [440, 0], {"jsCode": CRM}),
        node("c4", "Respond", "n8n-nodes-base.respondToWebhook", 1.1, [660, 0],
             {"respondWith": "text", "responseBody": "={{ $json.html }}",
              "options": {"responseHeaders": {"entries": [{"name": "Content-Type", "value": "text/html; charset=utf-8"}]}}}),
    ],
    "connections": link(("GET /crm", "Leads"), ("Leads", "Page"), ("Page", "Respond")),
}

out = ROOT / "n8n"
out.mkdir(exist_ok=True)
for name, wf in (("intake", intake), ("send", send), ("replies", replies), ("crm", crm)):
    json.dump({**wf, "active": False}, open(out / f"{name}.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("n8n/intake.json, send.json, replies.json, crm.json -> import them in n8n")
