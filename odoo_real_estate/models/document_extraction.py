"""Text / AI extraction helpers for the contract & license migration."""
import base64
import io
import json
import logging
import re
from datetime import datetime

_logger = logging.getLogger(__name__)

DOCUMENT_KINDS = [
    ('contract', "Contract / SPA"),
    ('trade_license', "Trade / Commercial License"),
    ('orn_certificate', "ORN Certificate"),
    ('brn_card', "BRN Card"),
    ('other', "Other"),
]

# field name -> (json type, description for the AI)
EXTRACTED_FIELDS = {
    'document_kind': ('kind', "Type of document"),
    # Contracts
    'contract_number': ('string', "Contract / agreement / SPA number"),
    'customer_name': ('string', "Buyer / purchaser / customer full name"),
    'customer_email': ('string', "Buyer email"),
    'customer_phone': ('string', "Buyer phone"),
    'agency_name': ('string', "Real estate agency / brokerage company name"),
    'broker_name': ('string', "Individual broker / agent name"),
    'project_name': ('string', "Real estate project / community name"),
    'unit_name': ('string', "Unit / apartment / villa number"),
    'contract_date': ('date', "Contract signature date"),
    'start_date': ('date', "Contract start date"),
    'end_date': ('date', "Contract end / expiry date"),
    'contract_value': ('number', "Total contract / sale value (number only)"),
    'currency': ('string', "Currency code, e.g. AED"),
    'payment_terms': ('string', "Payment plan / installment schedule as written"),
    'commission_details': ('string', "Commission rate / amount / conditions"),
    # Licenses / registrations
    'company_name': ('string', "Company name on the license"),
    'business_name': ('string', "Business / trade name"),
    'license_no': ('string', "License number"),
    'legal_type': ('string', "Legal type, e.g. Limited Liability Company"),
    'license_category': ('string', "License category / issuing authority"),
    'issue_date': ('date', "Issue date"),
    'expiry_date': ('date', "Expiry date"),
    'dcci_no': ('string', "DCCI membership number"),
    'commercial_reg_no': ('string', "Commercial registration number"),
    'main_license_no': ('string', "Main license number"),
    'orn_no': ('string', "RERA ORN (Office Registration Number)"),
    'brn_no': ('string', "RERA BRN (Broker Registration Number)"),
}

# Label variants found on Dubai licenses / certificates -> field
LABELS = [
    (r'company\s*name', 'company_name'),
    (r'business\s*name|trade\s*name', 'business_name'),
    (r'main\s*licen[cs]e\s*(?:no\.?|number)', 'main_license_no'),
    (r'licen[cs]e\s*(?:no\.?|number)', 'license_no'),
    (r'legal\s*(?:type|form)', 'legal_type'),
    (r'licen[cs]e\s*(?:category|type)', 'license_category'),
    (r'issue\s*date|date\s*of\s*issue', 'issue_date'),
    (r'expiry\s*date|date\s*of\s*expiry|valid\s*(?:until|till)', 'expiry_date'),
    (r'dcci\s*(?:membership)?\s*(?:no\.?|number)', 'dcci_no'),
    (r'commercial\s*registration\s*(?:no\.?|number)|register\s*no\.?', 'commercial_reg_no'),
    (r'orn(?:\s*(?:no\.?|number))?|office\s*registration\s*(?:no\.?|number)', 'orn_no'),
    (r'brn(?:\s*(?:no\.?|number))?|broker\s*registration\s*(?:no\.?|number)', 'brn_no'),
    (r'(?:contract|agreement|spa)\s*(?:no\.?|number|ref(?:erence)?)', 'contract_number'),
    (r'(?:purchaser|buyer|customer)(?:\s*name)?', 'customer_name'),
    (r'(?:agency|brokerage)(?:\s*name)?', 'agency_name'),
    (r'(?:broker|agent)(?:\s*name)?', 'broker_name'),
    (r'project(?:\s*name)?|community', 'project_name'),
    (r'unit(?:\s*(?:no\.?|number))?', 'unit_name'),
    (r'(?:contract|agreement)\s*date|date\s*of\s*(?:contract|agreement)', 'contract_date'),
    (r'start\s*date|commencement\s*date', 'start_date'),
    (r'end\s*date|completion\s*date', 'end_date'),
    (r'(?:contract|sale|purchase|total)\s*(?:value|price|amount)', 'contract_value'),
]

DATE_FORMATS = ('%d/%m/%Y', '%d-%m-%Y', '%Y-%m-%d', '%d.%m.%Y', '%d %b %Y', '%d %B %Y', '%b %d, %Y',
                '%B %d, %Y', '%d/%m/%y')


def parse_date(value):
    if not value:
        return False
    value = str(value).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    match = re.search(r'(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})', value)
    if match:
        try:
            return datetime(int(match[3]), int(match[2]), int(match[1])).date()
        except ValueError:
            return False
    return False


def parse_amount(value):
    if value in (None, False, ''):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    cleaned = re.sub(r'[^0-9.]', '', str(value).replace(',', ''))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def pdf_to_text(raw):
    """Text layer of a PDF (empty string for scanned documents)."""
    try:
        from pdfminer.high_level import extract_text
        return extract_text(io.BytesIO(raw)) or ''
    except Exception as e:  # noqa: BLE001 - any broken PDF must just fall back to AI / review
        _logger.info("PDF text extraction failed: %s", e)
        return ''


def detect_kind(text):
    low = (text or '').lower()
    if re.search(r'sale\s*(?:and|&)\s*purchase|\bspa\b|reservation\s*(?:form|agreement)|purchaser', low):
        return 'contract'
    if re.search(r'broker\s*registration|\bbrn\b', low):
        return 'brn_card'
    if re.search(r'office\s*registration|\born\b', low) and 'licen' not in low:
        return 'orn_certificate'
    if re.search(r'commercial\s*licen|trade\s*licen|licen[cs]e\s*no', low):
        return 'trade_license'
    if re.search(r'agreement|contract', low):
        return 'contract'
    return 'other'


def parse_labelled_text(text):
    """Rule based extraction for documents with 'Label : value' layouts (e.g. DED licenses)."""
    result = {}
    lines = [re.sub(r'\s+', ' ', line).strip() for line in (text or '').splitlines()]
    lines = [line for line in lines if line]
    for idx, line in enumerate(lines):
        for pattern, fname in LABELS:
            if fname in result:
                continue
            match = re.match(rf'^(?:{pattern})\s*[:\-]?\s*(.*)$', line, re.IGNORECASE)
            if not match:
                continue
            value = match[1].strip(' :-')
            if not value and idx + 1 < len(lines):
                value = lines[idx + 1]
            if value:
                result[fname] = value
            break
    for fname, (ftype, _desc) in EXTRACTED_FIELDS.items():
        if fname in result and ftype == 'date':
            result[fname] = parse_date(result[fname]) and parse_date(result[fname]).isoformat() or None
        elif fname in result and ftype == 'number':
            result[fname] = parse_amount(result[fname])
    result['document_kind'] = detect_kind(text)
    return result


def ai_schema():
    properties = {}
    for fname, (ftype, desc) in EXTRACTED_FIELDS.items():
        if ftype == 'kind':
            properties[fname] = {'type': 'string', 'enum': [k for k, _l in DOCUMENT_KINDS], 'description': desc}
        elif ftype == 'number':
            properties[fname] = {'type': ['number', 'null'], 'description': desc}
        elif ftype == 'date':
            properties[fname] = {'type': ['string', 'null'], 'description': desc + " (YYYY-MM-DD)"}
        else:
            properties[fname] = {'type': ['string', 'null'], 'description': desc}
    properties['confidence'] = {'type': 'number', 'description': "Overall confidence 0-1"}
    return {
        'type': 'object',
        'properties': properties,
        'required': list(properties),
        'additionalProperties': False,
    }


SYSTEM_PROMPT = (
    "You extract structured data from UAE real estate documents: sale & purchase agreements, reservation "
    "forms, agency agreements, Dubai DED commercial/trade licenses, RERA ORN certificates and BRN cards. "
    "Return only values printed in the document; use null when a value is absent. Dates must be ISO "
    "YYYY-MM-DD (documents use DD/MM/YYYY). Amounts are plain numbers without currency or separators. "
    "Keep names exactly as printed (including legal suffixes like L.L.C)."
)


DEFAULT_MODELS = {
    'anthropic': 'claude-opus-5-5',
    'openai': 'gpt-4.1',
    'google': 'gemini-2.5-pro',
}
REQUEST_TIMEOUT = 300  # multi-page scanned contracts take a while


class ExtractionError(Exception):
    pass


def get_ai_config(env):
    params = env['ir.config_parameter'].sudo()
    provider = params.get_param('odoo_real_estate.ai_provider') or 'anthropic'
    model = params.get_param('odoo_real_estate.ai_model') or DEFAULT_MODELS[provider]
    api_key = params.get_param('odoo_real_estate.ai_api_key')
    return provider, model, api_key


def ai_extract(env, raw, mimetype, text_hint=''):
    """Extract the document fields with the AI provider configured in Real Estate > Settings."""
    provider, model, api_key = get_ai_config(env)
    if not api_key:
        raise ExtractionError("No AI API key configured (Real Estate > Configuration > Settings).")
    prompt = "Extract the fields of the attached document."
    if text_hint:
        prompt += "\n\nText layer of the document (may be incomplete):\n" + text_hint[:20000]
    data = base64.b64encode(raw).decode()
    if provider == 'anthropic':
        payload = _extract_anthropic(api_key, model, data, mimetype, prompt)
    elif provider == 'openai':
        payload = _extract_openai(api_key, model, data, mimetype, prompt)
    else:
        payload = _extract_google(api_key, model, data, mimetype, prompt)
    payload = re.sub(r'^```(?:json)?|```$', '', (payload or '').strip()).strip()
    try:
        return json.loads(payload)
    except ValueError as e:
        raise ExtractionError(f"The AI answer is not valid JSON: {payload[:200]}") from e


def _extract_anthropic(api_key, model, data, mimetype, prompt):
    try:
        import anthropic
    except ImportError as e:
        raise ExtractionError(
            "The 'anthropic' Python package is missing. Install it in Odoo's Python (as administrator): "
            '"C:\\Program Files\\Odoo 19.0e.20260820\\python\\python.exe" -m pip install anthropic') from e
    if mimetype == 'application/pdf':
        file_block = {'type': 'document', 'source': {'type': 'base64', 'media_type': mimetype, 'data': data}}
    else:
        file_block = {'type': 'image', 'source': {'type': 'base64', 'media_type': mimetype, 'data': data}}
    client = anthropic.Anthropic(api_key=api_key, timeout=REQUEST_TIMEOUT)
    try:
        # Streaming keeps long (many-page) documents clear of HTTP timeouts
        with client.beta.messages.stream(
            model=model,
            max_tokens=16000,
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            system=SYSTEM_PROMPT,
            messages=[{'role': 'user', 'content': [file_block, {'type': 'text', 'text': prompt}]}],
            output_config={'format': {'type': 'json_schema', 'schema': ai_schema()}},
        ) as stream:
            message = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise ExtractionError("Invalid Anthropic API key.") from e
    except anthropic.RateLimitError as e:
        raise ExtractionError("Anthropic rate limit reached, the document will be retried later.") from e
    except anthropic.APIStatusError as e:
        raise ExtractionError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise ExtractionError("Cannot reach the Anthropic API (network).") from e
    if message.stop_reason == 'refusal':
        raise ExtractionError("The model declined to process this document.")
    if message.stop_reason == 'max_tokens':
        raise ExtractionError("The extraction was cut off (max tokens).")
    return next((b.text for b in message.content if b.type == 'text'), '')


def _http_post(url, headers, body):
    import requests
    try:
        response = requests.post(url, headers=headers, json=body, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise ExtractionError(f"Cannot reach the AI provider: {e}") from e
    if response.status_code >= 400:
        raise ExtractionError(f"AI provider error {response.status_code}: {response.text[:300]}")
    return response.json()


def _extract_openai(api_key, model, data, mimetype, prompt):
    if mimetype == 'application/pdf':
        file_part = {'type': 'input_file', 'filename': 'document.pdf',
                     'file_data': f'data:{mimetype};base64,{data}'}
    else:
        file_part = {'type': 'input_image', 'image_url': f'data:{mimetype};base64,{data}'}
    result = _http_post('https://api.openai.com/v1/responses', {'Authorization': f'Bearer {api_key}'}, {
        'model': model,
        'input': [
            {'role': 'system', 'content': [{'type': 'input_text', 'text': SYSTEM_PROMPT}]},
            {'role': 'user', 'content': [{'type': 'input_text', 'text': prompt}, file_part]},
        ],
        'text': {'format': {'type': 'json_schema', 'name': 'document_fields', 'schema': ai_schema(),
                            'strict': True}},
        'store': False,
    })
    return ''.join(c.get('text', '') for item in result.get('output', []) if item.get('type') == 'message'
                   for c in item.get('content', []))


def _extract_google(api_key, model, data, mimetype, prompt):
    fields_hint = json.dumps({k: v['description'] if 'description' in v else '' for k, v in
                              ai_schema()['properties'].items()})
    result = _http_post(
        f'https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent',
        {'x-goog-api-key': api_key},
        {
            'systemInstruction': {'parts': [{'text': SYSTEM_PROMPT}]},
            'contents': [{'role': 'user', 'parts': [
                {'inline_data': {'mime_type': mimetype, 'data': data}},
                {'text': prompt + "\n\nReturn one JSON object with exactly these keys (null when absent): "
                         + fields_hint},
            ]}],
            'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
        })
    parts = (result.get('candidates') or [{}])[0].get('content', {}).get('parts', [])
    return ''.join(p.get('text', '') for p in parts)
