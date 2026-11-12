import base64
import logging
import mimetypes
import os

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from . import document_extraction as extraction
from .document import normalize_reference
from .res_partner import normalize_company_name

_logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = ('.pdf', '.png', '.jpg', '.jpeg', '.webp')
KIND_TO_DOC_TYPE = {
    'trade_license': 'odoo_real_estate.doc_type_trade_license',
    'orn_certificate': 'odoo_real_estate.doc_type_orn',
    'brn_card': 'odoo_real_estate.doc_type_brn',
}
REQUIRED_FIELDS = {
    'contract': ['contract_number', 'contract_date', 'contract_value'],
    'trade_license': ['company_name', 'license_no', 'expiry_date'],
    'orn_certificate': ['company_name', 'orn_no'],
    'brn_card': ['broker_name', 'brn_no'],
}
LINE_STATES = [
    ('pending', "To Extract"), ('queued', "Queued"), ('extracted', "Extracted"),
    ('to_review', "To Review"), ('validated', "Validated"), ('duplicate', "Duplicate"),
    ('imported', "Imported"), ('error', "Error"),
]


class RealEstateMigrationBatch(models.Model):
    _name = 're.migration.batch'
    _description = "Contract / License Migration Batch"
    _inherit = ['mail.thread']
    _order = 'id desc'

    name = fields.Char(required=True, default=lambda self: _("Migration %s", fields.Date.context_today(self)))
    default_kind = fields.Selection(
        [('auto', "Detect automatically")] + extraction.DOCUMENT_KINDS, string="Document Type",
        default='auto', required=True)
    use_ai = fields.Boolean(string="Use AI Extraction", default=True,
                            help="Use the AI provider configured in Real Estate > Configuration > Settings "
                                 "(required for scanned documents).")
    create_missing_partners = fields.Boolean(
        string="Create Missing Customers / Agencies", default=True,
        help="When no existing contact matches, create it during import (after the duplicate check).")
    upload_ids = fields.Many2many('ir.attachment', 're_migration_batch_upload_rel', 'batch_id',
                                  'attachment_id', string="Upload Files")
    folder_path = fields.Char(
        string="Server Folder",
        help="Folder on the Odoo server containing the PDFs / images to import (administrators only).")
    line_ids = fields.One2many('re.migration.line', 'batch_id', string="Documents")
    line_count = fields.Integer(compute='_compute_counts')
    count_pending = fields.Integer(compute='_compute_counts')
    count_review = fields.Integer(compute='_compute_counts')
    count_validated = fields.Integer(compute='_compute_counts')
    count_duplicate = fields.Integer(compute='_compute_counts')
    count_imported = fields.Integer(compute='_compute_counts')
    count_error = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids.state')
    def _compute_counts(self):
        for batch in self:
            states = batch.line_ids.mapped('state')
            batch.line_count = len(states)
            batch.count_pending = sum(s in ('pending', 'queued', 'extracted') for s in states)
            batch.count_review = states.count('to_review')
            batch.count_validated = states.count('validated')
            batch.count_duplicate = states.count('duplicate')
            batch.count_imported = states.count('imported')
            batch.count_error = states.count('error')

    def action_add_uploads(self):
        for batch in self:
            for attachment in batch.upload_ids:
                attachment.write({'res_model': 're.migration.line', 'res_id': 0})
                self.env['re.migration.line'].create({'batch_id': batch.id, 'attachment_id': attachment.id})
            batch.upload_ids = [fields.Command.clear()]

    def action_load_folder(self):
        if not self.env.user.has_group('base.group_system'):
            raise UserError(_("Only administrators can import files from a server folder."))
        for batch in self:
            path = (batch.folder_path or '').strip().strip('"')
            if not path or not os.path.isdir(path):
                raise UserError(_("Folder not found on the server: %s", path))
            existing = set(batch.line_ids.mapped('filename'))
            count = 0
            for root, _dirs, files in os.walk(path):
                for filename in sorted(files):
                    if not filename.lower().endswith(SUPPORTED_EXTENSIONS) or filename in existing:
                        continue
                    with open(os.path.join(root, filename), 'rb') as f:
                        raw = f.read()
                    attachment = self.env['ir.attachment'].create({
                        'name': filename, 'raw': raw, 'res_model': 're.migration.line', 'res_id': 0,
                        'mimetype': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
                    })
                    self.env['re.migration.line'].create({'batch_id': batch.id, 'attachment_id': attachment.id})
                    count += 1
            batch.message_post(body=_("%s files loaded from %s", count, path))

    def action_extract(self):
        lines = self.line_ids.filtered(lambda l: l.state in ('pending', 'error'))
        if len(lines) <= 5:
            lines._extract()
        else:
            lines.write({'state': 'queued'})
            self.env.ref('odoo_real_estate.ir_cron_migration_queue')._trigger()
            self.message_post(body=_("%s documents queued for extraction. They are processed in the background.",
                                     len(lines)))

    def action_validate(self):
        self.line_ids.filtered(lambda l: l.state in ('extracted', 'to_review', 'validated', 'duplicate'))._validate()

    def action_import(self):
        lines = self.line_ids.filtered(lambda l: l.state == 'validated')
        if not lines:
            raise UserError(_("No validated documents to import."))
        lines._import()

    def _action_view_lines(self, states):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _("Documents"),
            'res_model': 're.migration.line',
            'view_mode': 'list,form',
            'domain': [('batch_id', '=', self.id)] + ([('state', 'in', states)] if states else []),
            'context': {'default_batch_id': self.id},
        }

    def action_view_all(self):
        return self._action_view_lines([])

    def action_view_review(self):
        return self._action_view_lines(['to_review', 'error'])

    def action_view_duplicates(self):
        return self._action_view_lines(['duplicate'])


class RealEstateMigrationLine(models.Model):
    _name = 're.migration.line'
    _description = "Migration Document"
    _order = 'batch_id, id'
    _rec_name = 'filename'

    batch_id = fields.Many2one('re.migration.batch', required=True, ondelete='cascade', index=True)
    attachment_id = fields.Many2one('ir.attachment', string="Original File", required=True, ondelete='restrict')
    filename = fields.Char(related='attachment_id.name', store=True)
    state = fields.Selection(LINE_STATES, default='pending', required=True, index=True)
    document_kind = fields.Selection(extraction.DOCUMENT_KINDS, string="Document Type")
    extraction_method = fields.Selection([('text', "Text layer / rules"), ('ai', "AI")], readonly=True)
    confidence = fields.Float(readonly=True)
    raw_text = fields.Text(readonly=True)
    raw_json = fields.Text(string="Raw Extraction", readonly=True)
    messages = fields.Text(string="Validation Messages", readonly=True)

    # Extracted values (editable during review)
    contract_number = fields.Char()
    customer_name = fields.Char()
    customer_email = fields.Char()
    customer_phone = fields.Char()
    agency_name = fields.Char()
    broker_name = fields.Char()
    project_name = fields.Char()
    unit_name = fields.Char()
    contract_date = fields.Date()
    start_date = fields.Date()
    end_date = fields.Date()
    contract_value = fields.Float()
    currency = fields.Char()
    payment_terms = fields.Text()
    commission_details = fields.Text()
    company_name = fields.Char()
    business_name = fields.Char()
    license_no = fields.Char()
    legal_type = fields.Char()
    license_category = fields.Char()
    issue_date = fields.Date()
    expiry_date = fields.Date()
    dcci_no = fields.Char(string="DCCI No.")
    commercial_reg_no = fields.Char(string="Commercial Reg. No.")
    main_license_no = fields.Char()
    orn_no = fields.Char(string="ORN")
    brn_no = fields.Char(string="BRN")

    # Mapping to Odoo records
    partner_id = fields.Many2one('res.partner', string="Customer")
    agency_id = fields.Many2one('res.partner', string="Agency", domain=[('is_company', '=', True)])
    broker_id = fields.Many2one('res.partner', string="Broker")
    project_id = fields.Many2one('re.project', string="Project")
    unit_id = fields.Many2one('re.unit', string="Unit", domain="[('project_id', '=', project_id)]")
    duplicate_line_id = fields.Many2one('re.migration.line', string="Duplicate of Document", readonly=True)
    duplicate_contract_id = fields.Many2one('re.contract', string="Existing Contract", readonly=True)
    contract_id = fields.Many2one('re.contract', string="Imported Contract", readonly=True)
    partner_document_id = fields.Many2one('re.partner.document', string="Imported Document", readonly=True)

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def _extract(self):
        company = self.env.company
        for line in self:
            try:
                with self.env.cr.savepoint():
                    line._extract_one(company)
            except Exception as e:  # noqa: BLE001 - keep processing the other documents
                _logger.warning("Extraction failed for %s: %s", line.filename, e)
                line.write({'state': 'error', 'messages': _("Extraction failed: %s", e)})

    def _extract_one(self, company):
        self.ensure_one()
        raw = self.attachment_id.raw or b''
        mimetype = self.attachment_id.mimetype or 'application/pdf'
        text = extraction.pdf_to_text(raw) if mimetype == 'application/pdf' else ''
        values, method = {}, 'text'
        if text.strip():
            values = extraction.parse_labelled_text(text)
        is_scanned = len(text.strip()) < 40
        needs_ai = is_scanned or values.get('document_kind') in ('contract', 'other') or not values
        if self.batch_id.use_ai and needs_ai:
            try:
                values = extraction.ai_extract(self.env, raw, mimetype, text_hint=text)
            except extraction.ExtractionError as e:
                if is_scanned or not values:
                    raise UserError(str(e)) from e
                # Text layer was readable: keep the rule-based values and let the reviewer complete them
                _logger.info("AI extraction unavailable for %s: %s", self.filename, e)
            else:
                method = 'ai'
        elif is_scanned:
            raise UserError(_("Scanned document without text layer: enable AI extraction for this batch."))
        vals = self._values_from_extraction(values)
        vals.update({'raw_text': text[:100000], 'raw_json': repr(values), 'extraction_method': method,
                     'confidence': float(values.get('confidence') or (0.6 if method == 'text' else 0.0)),
                     'state': 'extracted', 'messages': False})
        if self.batch_id.default_kind != 'auto':
            vals['document_kind'] = self.batch_id.default_kind
        self.write(vals)
        self._match_records()
        self._validate()

    def _values_from_extraction(self, values):
        vals = {'document_kind': values.get('document_kind') or 'other'}
        for fname, (ftype, _desc) in extraction.EXTRACTED_FIELDS.items():
            if fname == 'document_kind' or fname not in self._fields:
                continue
            value = values.get(fname)
            if ftype == 'date':
                vals[fname] = extraction.parse_date(value)
            elif ftype == 'number':
                vals[fname] = extraction.parse_amount(value)
            else:
                vals[fname] = (str(value).strip() if value not in (None, False) else False) or False
        return vals

    @api.model
    def _cron_process_queue(self):
        lines = self.search([('state', '=', 'queued')], limit=500)
        Cron = self.env['ir.cron']
        remaining = len(lines)
        for line in lines:
            line._extract()
            remaining -= 1
            if Cron._commit_progress(1, remaining=remaining) <= 0:
                break

    # ------------------------------------------------------------------
    # Matching & validation
    # ------------------------------------------------------------------

    def _find_agency(self):
        Partner = self.env['res.partner'].with_context(active_test=False)
        refs = [('re_trade_license_no', self.license_no or self.main_license_no), ('re_orn_no', self.orn_no)]
        for fname, ref in refs:
            ref = normalize_reference(ref)
            if ref:
                found = Partner.search([(fname, '!=', False)]).filtered(
                    lambda p: normalize_reference(p[fname]) == ref)
                if found:
                    return found[:1], 'reference'
        name = self.company_name or self.business_name or self.agency_name
        if name:
            normalized = normalize_company_name(name)
            found = Partner.search([('is_company', '=', True), ('re_normalized_name', '=', normalized)], limit=1)
            if found:
                return found, 'name'
            probe = Partner.new({'name': name, 'is_company': True})
            similar = probe._re_find_duplicates()
            if similar:
                return similar[:1], 'similar'
        return Partner.browse(), False

    def _match_records(self):
        Partner = self.env['res.partner']
        for line in self:
            vals = {}
            if not line.agency_id:
                agency, how = line._find_agency()
                if agency:
                    vals['agency_id'] = agency.id
            if line.document_kind == 'contract' and line.customer_name and not line.partner_id:
                customer = Partner.search([('name', '=ilike', line.customer_name.strip())], limit=1)
                if not customer and line.customer_email:
                    customer = Partner.search([('email', '=ilike', line.customer_email.strip())], limit=1)
                if customer:
                    vals['partner_id'] = customer.id
            if (line.brn_no or line.broker_name) and not line.broker_id:
                broker = Partner.browse()
                if line.brn_no:
                    ref = normalize_reference(line.brn_no)
                    broker = Partner.search([('re_brn_no', '!=', False)]).filtered(
                        lambda p: normalize_reference(p.re_brn_no) == ref)[:1]
                if not broker and line.broker_name:
                    broker = Partner.search([('name', '=ilike', line.broker_name.strip()),
                                             ('re_partner_type', '=', 'broker')], limit=1)
                if broker:
                    vals['broker_id'] = broker.id
            if line.project_name and not line.project_id:
                project = self.env['re.project'].search([('name', 'ilike', line.project_name.strip())], limit=1)
                if project:
                    vals['project_id'] = project.id
            project = self.env['re.project'].browse(vals.get('project_id')) or line.project_id
            if project and line.unit_name and not line.unit_id:
                unit = self.env['re.unit'].search([('project_id', '=', project.id),
                                                      ('name', '=ilike', line.unit_name.strip())], limit=1)
                if unit:
                    vals['unit_id'] = unit.id
            if vals:
                line.write(vals)

    def action_rematch(self):
        self._match_records()
        self._validate()

    def _validate(self):
        for line in self:
            errors, warnings = [], []
            kind = line.document_kind or 'other'
            if kind == 'other':
                errors.append(_("Document type not recognised: choose it manually."))
            for fname in REQUIRED_FIELDS.get(kind, []):
                if not line[fname]:
                    errors.append(_("Missing %s", line._fields[fname].string))
            if line.expiry_date and line.issue_date and line.expiry_date < line.issue_date:
                errors.append(_("Expiry date is before issue date."))
            if line.end_date and line.start_date and line.end_date < line.start_date:
                errors.append(_("End date is before start date."))
            if kind in KIND_TO_DOC_TYPE and line.expiry_date and line.expiry_date < fields.Date.context_today(self):
                warnings.append(_("Document already expired on %s.", line.expiry_date))
            if line.extraction_method == 'text' and kind == 'contract':
                warnings.append(_("Read with text rules only (no AI): check the values."))
            if line.extraction_method == 'ai' and line.confidence and line.confidence < 0.7:
                warnings.append(_("Low extraction confidence (%s%%): check the values.", round(line.confidence * 100)))

            # Duplicates -------------------------------------------------
            duplicate_line = duplicate_contract = False
            key_field = {'contract': 'contract_number', 'trade_license': 'license_no',
                         'orn_certificate': 'orn_no', 'brn_card': 'brn_no'}.get(kind)
            key = normalize_reference(line[key_field]) if key_field else ''
            if key:
                others = self.search([('id', '!=', line.id), ('document_kind', '=', kind),
                                      (key_field, '!=', False), ('state', '!=', 'error')])
                same = others.filtered(lambda o: normalize_reference(o[key_field]) == key
                                       and o.state != 'duplicate')
                if same:
                    duplicate_line = same.sorted('id')[0]
                    if duplicate_line.id > line.id:
                        duplicate_line = False  # the oldest document is the original
            if kind == 'contract' and key:
                existing = self.env['re.contract'].search([]).filtered(
                    lambda c: normalize_reference(c.name) == key and c.migration_line_id != line)
                duplicate_contract = existing[:1]

            # Mapping ------------------------------------------------------
            if kind in ('trade_license', 'orn_certificate') and not line.agency_id \
                    and not line.batch_id.create_missing_partners:
                errors.append(_("No matching agency."))
            if kind == 'brn_card' and not line.agency_id and not line.broker_id:
                warnings.append(_("Broker's agency not identified."))
            if kind == 'contract':
                if not line.partner_id and not line.batch_id.create_missing_partners:
                    errors.append(_("No matching customer."))
                if line.project_name and not line.project_id:
                    warnings.append(_("Project '%s' not found: the text is kept on the contract.", line.project_name))
            if line.agency_id and line.agency_id.re_partner_type != 'agency' and kind != 'contract':
                warnings.append(_("Matched contact %s is not flagged as agency yet; it will be.", line.agency_id.name))

            if duplicate_line or duplicate_contract:
                state = 'duplicate'
                errors.insert(0, _("Duplicate of %s",
                                   duplicate_contract.name if duplicate_contract else duplicate_line.filename))
            elif errors or warnings:
                state = 'to_review'
            else:
                state = 'validated'
            line.write({
                'state': state,
                'duplicate_line_id': duplicate_line.id if duplicate_line else False,
                'duplicate_contract_id': duplicate_contract.id if duplicate_contract else False,
                'messages': '\n'.join(errors + warnings) or False,
            })

    def action_validate(self):
        self._validate()

    def action_approve(self):
        """Reviewer accepts the values despite warnings."""
        blocking = self.filtered(lambda l: l.state == 'duplicate' or not l.document_kind or l.document_kind == 'other')
        if blocking:
            raise UserError(_("Duplicates and documents of unknown type cannot be approved."))
        for line in self:
            missing = [f for f in REQUIRED_FIELDS.get(line.document_kind, []) if not line[f]]
            if missing:
                raise UserError(_("%(file)s: fill %(fields)s first.", file=line.filename,
                                  fields=', '.join(line._fields[f].string for f in missing)))
        self.write({'state': 'validated'})

    def action_extract(self):
        self._extract()

    def action_import(self):
        self.filtered(lambda l: l.state == 'validated')._import()

    # ------------------------------------------------------------------
    # Import
    # ------------------------------------------------------------------

    def _import(self):
        for line in self:
            try:
                with self.env.cr.savepoint():
                    # Records created by earlier documents of the same run (e.g. the agency of a
                    # license imported just before) must be reused, not duplicated
                    line._match_records()
                    if line.document_kind == 'contract':
                        line._import_contract()
                    else:
                        line._import_registration_document()
                    line.state = 'imported'
            except Exception as e:  # noqa: BLE001
                line.write({'state': 'to_review', 'messages': _("Import failed: %s", e)})

    def _copy_attachment_to(self, record):
        return self.attachment_id.copy({'res_model': record._name, 'res_id': record.id})

    def _get_or_create_agency(self):
        agency = self.agency_id
        if not agency:
            if not self.batch_id.create_missing_partners:
                raise UserError(_("No agency matched."))
            agency = self.env['res.partner'].create({
                'name': self.company_name or self.business_name or self.agency_name,
                'is_company': True,
                're_partner_type': 'agency',
                're_registration_state': 'draft',
            })
            self.agency_id = agency
        vals = {}
        if agency.re_partner_type != 'agency':
            vals['re_partner_type'] = 'agency'
        if not agency.re_trade_license_no and (self.license_no or self.main_license_no):
            vals['re_trade_license_no'] = self.license_no or self.main_license_no
        if not agency.re_orn_no and self.orn_no:
            vals['re_orn_no'] = self.orn_no
        if not agency.company_registry and self.commercial_reg_no:
            vals['company_registry'] = self.commercial_reg_no
        if vals:
            agency.write(vals)
        return agency

    def _import_registration_document(self):
        self.ensure_one()
        if self.document_kind == 'brn_card':
            owner = self.broker_id
            if not owner:
                if not self.batch_id.create_missing_partners:
                    raise UserError(_("No broker matched."))
                owner = self.env['res.partner'].create({
                    'name': self.broker_name, 're_partner_type': 'broker',
                    'parent_id': self.agency_id.id, 're_brn_no': self.brn_no,
                })
                self.broker_id = owner
            elif not owner.re_brn_no:
                owner.re_brn_no = self.brn_no
            number = self.brn_no
        else:
            owner = self._get_or_create_agency()
            number = self.orn_no if self.document_kind == 'orn_certificate' else (self.license_no or self.main_license_no)
        doc_type = self.env.ref(KIND_TO_DOC_TYPE[self.document_kind])
        attachment = self._copy_attachment_to(owner)
        doc = owner.re_document_ids.filtered(lambda d: d.type_id == doc_type)[:1]
        note = '\n'.join(filter(None, [
            self.legal_type and _("Legal type: %s", self.legal_type),
            self.license_category and _("Category: %s", self.license_category),
            self.dcci_no and _("DCCI No.: %s", self.dcci_no),
            self.commercial_reg_no and _("Commercial Reg. No.: %s", self.commercial_reg_no),
        ]))
        vals = {'document_number': number, 'issue_date': self.issue_date, 'expiry_date': self.expiry_date,
                'attachment_ids': [fields.Command.link(attachment.id)], 'notes': note or False}
        if doc:
            if doc.expiry_date and self.expiry_date and self.expiry_date < doc.expiry_date:
                # Older copy of the document: keep the file for history only
                doc.attachment_ids = [fields.Command.link(attachment.id)]
            else:
                doc.write(vals)
        else:
            doc = self.env['re.partner.document'].create(dict(vals, partner_id=owner.id, type_id=doc_type.id))
        self.partner_document_id = doc

    def _import_contract(self):
        self.ensure_one()
        partner = self.partner_id
        if not partner and self.customer_name:
            partner = self.env['res.partner'].create({
                'name': self.customer_name, 'email': self.customer_email or False,
                'phone': self.customer_phone or False, 're_partner_type': 'customer',
            })
            self.partner_id = partner
        agency = self.agency_id
        if not agency and self.agency_name and self.batch_id.create_missing_partners:
            agency = self.env['res.partner'].create({'name': self.agency_name, 'is_company': True,
                                                     're_partner_type': 'agency'})
            self.agency_id = agency
        currency = self.env['res.currency'].search([('name', '=', (self.currency or '').upper())], limit=1) \
            if self.currency else self.env.company.currency_id
        contract = self.env['re.contract'].create({
            'name': self.contract_number,
            'contract_type': 'spa',
            'partner_id': partner.id,
            'agency_id': agency.id if agency and agency.re_partner_type == 'agency' else False,
            'broker_id': self.broker_id.id if self.broker_id.re_partner_type == 'broker' else False,
            'project_id': self.project_id.id,
            'unit_id': self.unit_id.id,
            'property_description': ' / '.join(filter(None, [self.project_name, self.unit_name])) or False,
            'contract_date': self.contract_date,
            'start_date': self.start_date,
            'end_date': self.end_date,
            'contract_value': self.contract_value,
            'currency_id': (currency or self.env.company.currency_id).id,
            'payment_terms': self.payment_terms,
            'commission_details': self.commission_details,
            'license_details': '\n'.join(filter(None, [
                self.license_no and _("License No.: %s", self.license_no),
                self.orn_no and _("ORN: %s", self.orn_no), self.brn_no and _("BRN: %s", self.brn_no)])) or False,
            'migration_line_id': self.id,
        })
        attachment = self._copy_attachment_to(contract)
        contract.message_post(body=_("Imported from %s", self.filename), attachment_ids=attachment.ids)
        self.contract_id = contract
