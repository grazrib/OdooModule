from unittest.mock import patch
import base64

from lxml import etree

from odoo.tests import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon


@tagged("post_install", "-at_install")
class TestPecSdiMatching(AccountTestInvoicingCommon):
    def test_invoice_filename_from_notification_filename(self):
        mt = self.env["mail.thread"]
        self.assertEqual(
            mt._invoice_filename_from_notification_filename(
                "IT12345670017_1000U_RC_001.xml"
            ),
            "IT12345670017_1000U.xml",
        )

    def test_find_invoice_by_xml_filename_with_progressive_only_attachment(self):
        company = self.env.company
        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")

        move = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )

        attachment = self.env["ir.attachment"].create(
            {
                "name": "1000U.xml",
                "type": "binary",
                "mimetype": "application/xml",
                "raw": b"<xml/>",
                "res_model": "account.move",
                "res_id": move.id,
                "res_field": "l10n_it_edi_attachment_file",
                "company_id": company.id,
            }
        )
        move.invalidate_recordset(fnames=["l10n_it_edi_attachment_id"])

        found = self.env["mail.thread"]._find_invoice_by_xml_filename(
            "IT12345670017_1000U.xml"
        )
        self.assertEqual(found, move)
        self.assertEqual(move.l10n_it_edi_attachment_id, attachment)

    def test_find_invoice_by_xml_filename_uses_filename_prefix_before_progressive(self):
        company = self.env.company
        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")

        move_a = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )
        self.env["ir.attachment"].create(
            {
                "name": "IT12345670017_1000U.xml",
                "type": "binary",
                "mimetype": "application/xml",
                "raw": b"<xml/>",
                "res_model": "account.move",
                "res_id": move_a.id,
                "res_field": "l10n_it_edi_attachment_file",
                "company_id": company.id,
            }
        )
        move_a.invalidate_recordset(fnames=["l10n_it_edi_attachment_id"])

        move_b = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )
        self.env["ir.attachment"].create(
            {
                "name": "IT99999999999_1000U.xml",
                "type": "binary",
                "mimetype": "application/xml",
                "raw": b"<xml/>",
                "res_model": "account.move",
                "res_id": move_b.id,
                "res_field": "l10n_it_edi_attachment_file",
                "company_id": company.id,
            }
        )
        move_b.invalidate_recordset(fnames=["l10n_it_edi_attachment_id"])

        found = self.env["mail.thread"]._find_invoice_by_xml_filename(
            "IT12345670017_1000U_RC_002.xml"
        )
        self.assertEqual(found, move_a)

    def test_normalize_attachment_filename_from_xml(self):
        company = self.env.company
        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")

        move = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )

        xml = (
            b"<FatturaElettronica><FatturaElettronicaHeader><DatiTrasmissione>"
            b"<IdTrasmittente><IdPaese>IT</IdPaese><IdCodice>12345670017</IdCodice></IdTrasmittente>"
            b"<ProgressivoInvio>1000U</ProgressivoInvio>"
            b"</DatiTrasmissione></FatturaElettronicaHeader></FatturaElettronica>"
        )

        attachment = self.env["ir.attachment"].create(
            {
                "name": "1000U.xml",
                "type": "binary",
                "mimetype": "application/xml",
                "raw": xml,
                "res_model": "account.move",
                "res_id": move.id,
                "res_field": "l10n_it_edi_attachment_file",
                "company_id": company.id,
            }
        )

        move._l10n_it_edi_pec_normalize_attachment_filename(attachment)
        self.assertEqual(attachment.name, "IT12345670017_1000U.xml")

    def test_parse_pec_notification_rc_updates_state_and_attaches_xml(self):
        company = self.env.company
        company.partner_id.write(
            {
                "country_id": self.env.ref("base.it").id,
                "l10n_it_codice_fiscale": "12345670017",
            }
        )

        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")

        move = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )

        xml = (
            b"<RicevutaConsegna><IdentificativoSdI>12345</IdentificativoSdI>"
            b"<Descrizione>Consegna effettuata</Descrizione>"
            b"<DataOraConsegna>2025-01-01T10:00:00</DataOraConsegna>"
            b"</RicevutaConsegna>"
        )

        content = base64.b64encode(xml)
        fname = "IT12345670017_1000U_RC_001.xml"
        message_dict = {
            "subject": "Notifica RC IT12345670017_1000U_RC_001.xml",
            "attachments": [
                {"fname": fname, "content": content},
            ],
        }

        notification_data = move._l10n_it_edi_parse_pec_notification(message_dict)

        self.assertTrue(notification_data)
        self.assertEqual(notification_data["type"], "RC")
        self.assertEqual(move.l10n_it_edi_state, "forwarded")
        self.assertEqual(move.l10n_it_edi_pec_state, "delivered")

        messages = move.message_ids.sorted("id")
        self.assertTrue(messages)
        last = messages[-1]
        self.assertIn("Risposta SdI RC", last.body)
        self.assertIn("Consegna effettuata al destinatario", last.body)
        self.assertTrue(last.attachment_ids)

    def test_parse_pec_notification_fallback_ns_attaches_once(self):
        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")

        move = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )

        xml = b"this is not xml at all"
        content = base64.b64encode(xml)
        fname = "IT12345670017_1000U_NS_001.xml"
        message_dict = {
            "subject": "Notifica NS IT12345670017_1000U_NS_001.xml",
            "attachments": [
                {"fname": fname, "content": content},
                {"fname": fname, "content": content},
            ],
        }

        notification_data = move._l10n_it_edi_parse_pec_notification(message_dict)

        self.assertTrue(notification_data)
        self.assertEqual(notification_data["type"], "NS")
        self.assertEqual(move.l10n_it_edi_state, "rejected")
        self.assertEqual(move.l10n_it_edi_pec_state, "error")

        messages = move.message_ids.sorted("id")
        self.assertTrue(messages)
        last = messages[-1]
        self.assertIn("Risposta SdI NS", last.body)
        self.assertEqual(len(last.attachment_ids), 1)

    def test_pec_export_uses_random_progressivo_and_keeps_xml_consistent(self):
        company = self.env.company
        company.partner_id.write(
            {
                "country_id": self.env.ref("base.it").id,
                "l10n_it_codice_fiscale": "12345670017",
            }
        )
        company.write({"l10n_it_edi_use_pec": True})

        partner = self.env.ref("base.res_partner_1")
        product = self.env.ref("product.product_product_10")
        move = self.init_invoice(
            "out_invoice",
            partner=partner,
            products=product,
            taxes=self.tax_sale_a,
        )

        with patch.object(type(move), "_l10n_it_edi_pec_generate_progressivo", return_value="gHtDJ"):
            vals = move._l10n_it_edi_get_attachment_values(pdf_values=None)

        self.assertEqual(vals["name"], "IT12345670017_gHtDJ.xml")
        root = etree.fromstring(vals["raw"])
        progressive = (
            root.xpath(
                'string(//*[local-name()="DatiTrasmissione"]/*[local-name()="ProgressivoInvio"][1])'
            )
            or ""
        ).strip()
        self.assertEqual(progressive, "gHtDJ")
