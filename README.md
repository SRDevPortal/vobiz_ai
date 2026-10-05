# Vobiz AI

Vobiz AI telephony integration for ERPNext/Frappe CRM.

## Customer-number privacy

When Privacy Shield is installed, its site switch is enabled, and a user lacks
View Full, Vobiz AI masks customer numbers in related-call responses and removes
recording links, transcripts, summaries, and other free text that can repeat an
original number. Direct HTTP access to raw call, error, and webhook records is
blocked, and recording download or streaming endpoints are denied.

Patient-routing realtime notifications are projected for the receiving agent:
the customer number and phone-like text in the patient label are masked while
call, patient, routing, DID, and agent identities remain available. Full-view
users retain the existing response contract. Provider-authenticated voice-agent
configuration, webhook processing, routing, and stored records keep original
numbers.
