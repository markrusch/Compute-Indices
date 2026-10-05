# Personal data breach procedure (GDPR Art. 33 and 34)

> **Status: DRAFT — not adopted, not in force, not legal advice.**
> Prepared for review by counsel before adoption. Nothing here is a statement of
> compliance with any law, regulation or standard. It restates statutory duties as the
> administrator understands them and adds no commitment beyond them.

Controller: Mark Rusch. Supervisory authority: Autoriteit Persoonsgegevens (AP),
https://autoriteitpersoonsgegevens.nl/. The AP's online form for reporting a breach is
found from that site `[TO VERIFY: current address of the form]`.

A personal data breach is a breach of security leading to accidental or unlawful
destruction, loss, alteration, unauthorised disclosure of, or access to, personal data
(Art. 4(12)). It covers confidentiality, integrity and availability. Loss of a laptop
holding the private contributor store is a breach; so is an email sent to the wrong
person, a leaked API key that exposes stored data, or the deletion of the only copy of
the identity key.

Where personal data is at stake, the systems are those in the
[register](gdpr-records-of-processing.md): the mailbox, the private contributor store and
key, the Upstash visit log, Vercel and Resend, and the GitHub account.

## Steps

### 1. Detect and record the time

Sources: a provider's security notice, an alert from GitHub secret scanning, a report from
a contributor or visitor, or the administrator's own discovery. Write down the moment the
administrator became aware. The 72-hour period runs from awareness of a breach, which
means reasonable certainty that a security incident has compromised personal data. Do not
wait for the investigation to end before starting the log entry.

### 2. Contain

Stop it getting worse, and do not destroy evidence:

- Rotate the exposed secret: Resend API key, Upstash token, `ANALYTICS_SALT`, `STATS_KEY`,
  `GITHUB_DISPATCH_TOKEN`, source API tokens, account passwords.
- Revoke sessions and tokens on the affected account; disable the affected function.
- Take a lost device's accounts out of use; where the private store was on it, note its
  contents and whether the disk was encrypted.
- If personal data was committed to the public repository, remove it from the current tree
  and record that history and forks may still hold it. Rewriting history in this
  repository is forbidden without a separate decision, because it holds published
  pricing history `[TO CONFIRM: with counsel, the response for personal data in history]`.
- Ask a recipient of a misdirected email to delete it and confirm.

### 3. Assess

Answer, and write the answers into the log:

1. What data, which categories, roughly how many people and records.
2. Was it confidential, altered or lost; is a copy recoverable.
3. Who could have had it, and for how long.
4. Was it protected in a way that makes it unintelligible (encryption with a key not
   exposed; a salted hash whose salt was not exposed).
5. Likely consequences for the individuals.
6. Risk level: no risk likely; risk; high risk.

Contact-person details of business contributors, taken alone, will often be low risk.
Free-text messages from the contact form, or a leaked contributor-to-pseudonym key
combined with prices, may be higher. Judgement is the administrator's, with counsel where
in doubt `[COUNSEL: on-call arrangement]`.

### 4. Decide on notification

| Assessment | Authority (Art. 33) | Individuals (Art. 34) |
|---|---|---|
| Unlikely to result in a risk to individuals | Not required. Log the reasoning | No |
| Risk | Required, without undue delay and where feasible within 72 hours of awareness | Not required unless the risk is high |
| High risk | As above | Required, without undue delay, subject to the exceptions in Art. 34(3): data rendered unintelligible; later measures mean high risk is no longer likely; disproportionate effort, in which case a public communication instead |

If the 72 hours are missed, the notification says why (Art. 33(1)). If not everything is
known in time, notify with what is known and supplement in phases (Art. 33(4)).

### 5. Notify the authority

Content required by Art. 33(3): nature of the breach with categories and approximate
numbers of individuals and records; the administrator's contact details; likely
consequences; measures taken or proposed.

### 6. Notify individuals where required

Plain language. Describe the nature of the breach and give the contact point, likely
consequences and measures taken (Art. 34(2)). Send directly; use a public notice only under
the disproportionate-effort exception.

### 7. Processors

A processor must tell the controller without undue delay after becoming aware of a breach
(Art. 33(2)). Awareness for the 72-hour period may run from the administrator receiving
the provider's notice. Keep every provider notice in the log entry. If a provider
publishes an incident affecting the site, check whether personal data of TCI's visitors,
correspondents or contributors was in scope, and log the check even if the answer is no.

### 8. Log every breach, notified or not

Art. 33(5) requires documentation of every breach: the facts, its effects and the remedial
action. Keep the log in a private location outside the repository, because entries may
identify people or describe weaknesses `[TO CONFIRM: location]`. A breach judged unlikely
to result in risk is entered with the reasoning.

### 9. Close and review

After containment, record the cause, what changed, and whether this document, the
register or the DPIA needs revising. A weakness found in the code or configuration is
handled as a defect in the ordinary way; do not describe it in a public file until fixed.

## Breach log template

Copy one block per incident.

```
Breach ID:                  BR-YYYY-NNN
Recorded by / date:         
Became aware (UTC, time):   
How detected:               
Occurred (UTC, best estimate; start and end):
Systems and data sets:      
Categories of data:         
Categories and approx. number of individuals:
Approx. number of records:  
Nature: confidentiality / integrity / availability
Cause:                      
Encrypted or hashed, and was the key exposed:
Processors involved, and date each notified TCI:
Containment steps and times:
Likely consequences:        
Risk assessment: none likely / risk / high risk
Reasoning:                  
Advice taken (counsel):     
Notified AP:                yes / no   Date and time (UTC):   Reference:
  If after 72 hours, reasons for delay:
  Supplementary notifications:
Notified individuals:       yes / no   Date, method, number:
  If not, which Art. 34(3) exception or the reason risk is not high:
Remedial action and date completed:
Changes to procedures or documents:
Closed on / by:             
```
