import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


def normalize_reference(value):
    """Normalize a license / registration number for duplicate comparison."""
    return re.sub(r'[^0-9a-z]', '', (value or '').lower())


class RealEstateDocumentType(models.Model):
    _name = 're.document.type'
    _description = "Registration Document Type"
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    code = fields.Char(required=True, help="Technical code, e.g. TL, ORN, BRN.")
    sequence = fields.Integer(default=10)
    applies_to = fields.Selection(
        [('agency', "Agency"), ('broker', "Broker"), ('both', "Agency & Broker")],
        required=True, default='agency')
    is_required = fields.Boolean(
        string="Required for Registration", default=True,
        help="The registration cannot be activated without this document, "
             "and it is suspended/cancelled when this document expires.")
    requires_expiry = fields.Boolean(string="Has Expiry Date", default=True)
    reminder_days = fields.Char(
        string="Reminder Days Before Expiry", default='60,30,7',
        help="Comma separated list of days before expiry at which the Relationship "
             "Manager receives a renewal reminder.")
    grace_days = fields.Integer(
        string="Grace Period (days)", default=0,
        help="Days after expiry before the registration is suspended/cancelled.")
    active = fields.Boolean(default=True)

    _code_uniq = models.Constraint('UNIQUE(code)', "The document type code must be unique.")

    @api.constrains('reminder_days')
    def _check_reminder_days(self):
        for doc_type in self:
            try:
                doc_type._get_reminder_days()
            except ValueError:
                raise ValidationError(_("Reminder days must be a comma separated list of numbers, e.g. 60,30,7."))

    def _get_reminder_days(self):
        self.ensure_one()
        days = {int(d) for d in (self.reminder_days or '').split(',') if d.strip()}
        return sorted((d for d in days if d > 0), reverse=True)


class RealEstatePartnerDocument(models.Model):
    _name = 're.partner.document'
    _description = "Agency / Broker Document"
    _order = 'expiry_date, id'
    _rec_name = 'type_id'

    partner_id = fields.Many2one('res.partner', required=True, ondelete='cascade', index=True)
    partner_type = fields.Selection(related='partner_id.re_partner_type')
    rm_id = fields.Many2one(related='partner_id.re_rm_id', store=True, string="Relationship Manager")
    type_id = fields.Many2one('re.document.type', string="Document", required=True)
    type_code = fields.Char(related='type_id.code')
    requires_expiry = fields.Boolean(related='type_id.requires_expiry')
    document_number = fields.Char(string="Number")
    issue_date = fields.Date()
    expiry_date = fields.Date(index=True)
    attachment_ids = fields.Many2many(
        'ir.attachment', 're_partner_document_attachment_rel', 'document_id', 'attachment_id',
        string="Files")
    days_to_expiry = fields.Integer(compute='_compute_days_to_expiry')
    state = fields.Selection(
        [('valid', "Valid"), ('expiring', "Expiring Soon"), ('expired', "Expired"),
         ('no_expiry', "No Expiry"), ('missing_date', "Expiry Date Missing")],
        compute='_compute_state', store=True, index=True)
    renewal_state = fields.Selection(
        [('none', "Not Required"), ('pending', "Renewal Pending"),
         ('in_progress', "Renewal In Progress"), ('renewed', "Renewed")],
        default='none', required=True)
    last_reminder_threshold = fields.Integer(
        help="Smallest reminder threshold (in days) already notified for the current expiry date.")
    notes = fields.Text()

    @api.depends('expiry_date')
    def _compute_days_to_expiry(self):
        today = fields.Date.context_today(self)
        for doc in self:
            doc.days_to_expiry = (doc.expiry_date - today).days if doc.expiry_date else 0

    @api.depends('expiry_date', 'type_id.requires_expiry', 'type_id.reminder_days')
    def _compute_state(self):
        for doc in self:
            doc.state = doc._get_expiry_state()

    def _get_expiry_state(self):
        self.ensure_one()
        if not self.type_id.requires_expiry:
            return 'no_expiry'
        if not self.expiry_date:
            return 'missing_date'
        days_left = (self.expiry_date - fields.Date.context_today(self)).days
        if days_left < 0:
            return 'expired'
        reminder_days = self.type_id._get_reminder_days()
        if reminder_days and days_left <= reminder_days[0]:
            return 'expiring'
        return 'valid'

    @api.constrains('partner_id', 'type_id')
    def _check_one_document_per_type(self):
        for doc in self:
            if self.search_count([('partner_id', '=', doc.partner_id.id), ('type_id', '=', doc.type_id.id),
                                  ('id', '!=', doc.id)]):
                raise ValidationError(_(
                    "%(partner)s already has a %(type)s document. Update the existing one when it is renewed.",
                    partner=doc.partner_id.display_name, type=doc.type_id.name))

    @api.constrains('document_number', 'type_id')
    def _check_duplicate_number(self):
        if self.env.context.get('re_skip_duplicate_check'):
            return
        for doc in self.filtered('document_number'):
            number = normalize_reference(doc.document_number)
            others = self.search([('type_id', '=', doc.type_id.id), ('id', '!=', doc.id),
                                  ('partner_id', '!=', doc.partner_id.id), ('document_number', '!=', False)])
            duplicate = others.filtered(lambda d: normalize_reference(d.document_number) == number)
            if duplicate:
                raise ValidationError(_(
                    "%(type)s number %(number)s is already registered for %(partner)s.",
                    type=doc.type_id.name, number=doc.document_number,
                    partner=duplicate[0].partner_id.display_name))

    @api.onchange('type_id')
    def _onchange_type_id(self):
        # Pre-fill the number with the identifier already known on the profile
        if self.type_id and not self.document_number and self.partner_id:
            self.document_number = self.partner_id._re_reference_for_code(self.type_id.code)

    @api.model_create_multi
    def create(self, vals_list):
        docs = super().create(vals_list)
        docs.partner_id._re_sync_references_from_documents()
        return docs

    def write(self, vals):
        if 'expiry_date' in vals:
            vals = dict(vals, last_reminder_threshold=0)
        old_expiry = {doc.id: doc.expiry_date for doc in self}
        res = super().write(vals)
        if 'expiry_date' in vals:
            renewed = self.filtered(lambda d: d.expiry_date and old_expiry[d.id]
                                    and d.expiry_date > old_expiry[d.id])
            if renewed:
                renewed.renewal_state = 'renewed'
                for doc in renewed:
                    doc.partner_id.message_post(body=_(
                        "%(type)s renewed, new expiry date: %(date)s",
                        type=doc.type_id.name, date=doc.expiry_date))
            self.partner_id._re_reactivate_if_compliant()
        if 'document_number' in vals:
            self.partner_id._re_sync_references_from_documents()
        return res

    def action_start_renewal(self):
        self.renewal_state = 'in_progress'

    # ------------------------------------------------------------------
    # Scheduled action
    # ------------------------------------------------------------------

    @api.model
    def _cron_check_expiry(self):
        """Daily: refresh states, send renewal reminders, apply expiry rules."""
        today = fields.Date.context_today(self)
        docs = self.search([('type_id.requires_expiry', '=', True), ('expiry_date', '!=', False)])
        for doc in docs:
            new_state = doc._get_expiry_state()
            if doc.state != new_state:
                doc.state = new_state
            if new_state in ('expiring', 'expired') and doc.renewal_state in ('none', 'renewed'):
                doc.renewal_state = 'pending'
            doc._send_renewal_reminder(today)

        expired_partners = docs.filtered(
            lambda d: d.type_id.is_required and d.state == 'expired'
            and (today - d.expiry_date).days >= d.type_id.grace_days
        ).partner_id
        expired_partners._re_apply_expiry_rule()

    def _send_renewal_reminder(self, today):
        self.ensure_one()
        days_left = (self.expiry_date - today).days
        thresholds = [t for t in self.type_id._get_reminder_days() if days_left <= t]
        if days_left < 0:
            thresholds.append(-1)
        if not thresholds:
            return
        threshold = min(thresholds)
        if self.last_reminder_threshold and self.last_reminder_threshold <= threshold:
            return  # this reminder was already sent
        partner = self.partner_id
        user = partner.re_rm_id or partner.re_territory_id.manager_id or self.env.user
        if days_left < 0:
            summary = _("%(type)s EXPIRED for %(partner)s", type=self.type_id.name, partner=partner.name)
        else:
            summary = _("%(type)s of %(partner)s expires in %(days)s days",
                        type=self.type_id.name, partner=partner.name, days=days_left)
        partner.activity_schedule(
            'mail.mail_activity_data_todo',
            date_deadline=min(self.expiry_date, today) if days_left < 0 else self.expiry_date,
            summary=summary,
            note=_("Please collect the renewed %(type)s (current expiry %(date)s) and update the profile.",
                   type=self.type_id.name, date=self.expiry_date),
            user_id=user.id,
        )
        partner.message_post(body=summary)
        self.last_reminder_threshold = threshold
