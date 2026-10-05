import unittest
from unittest.mock import patch
from vobiz_ai.number_privacy import project_response

class NumberPrivacyTests(unittest.TestCase):
    def test_restricted_response_does_not_change_provider_input(self):
        raw = {"customer_number": "9876501234", "customer_phone": "9876501234",
               "recording_url": "https://example.invalid/audio", "transcript_text": "9876501234",
               "ai_summary": "Call 9876501234", "queue_status": "Pending"}
        with patch('vobiz_ai.number_privacy.restricted', return_value=True):
            result = project_response(raw)
        self.assertNotIn('9876501234', str(result))
        self.assertEqual(result['queue_status'], 'Pending')
        self.assertEqual(raw['customer_phone'], '9876501234')
        self.assertTrue(raw['recording_url'])

    def test_full_visibility_preserves_response(self):
        raw = [{"customer_number": "9876501234", "recording_url": "audio"}]
        with patch('vobiz_ai.number_privacy.restricted', return_value=False):
            self.assertEqual(project_response(raw), raw)

    def test_related_calls_denied_before_query(self):
        import frappe
        from vobiz_ai.api import processing
        with patch.object(processing, 'frappe') as fake:
            fake.get_doc.return_value.check_permission.side_effect = frappe.PermissionError
            with self.assertRaises(frappe.PermissionError):
                processing.get_related_calls.__wrapped__('Patient', 'not-allowed')
            fake.get_all.assert_not_called()

    def test_related_calls_masks_after_authorization(self):
        import frappe
        from vobiz_ai.api import processing
        with patch.object(processing, 'frappe') as fake, patch('vobiz_ai.number_privacy.restricted', return_value=True):
            fake.get_all.return_value = [frappe._dict(name='call',customer_number='9876501234',recording_url='',ai_summary='9876501234')]
            result=processing.get_related_calls.__wrapped__('Patient','allowed')
            fake.get_doc.return_value.check_permission.assert_called_once_with('read')
            self.assertNotIn('9876501234',str(result))

    def test_recordings_denied_before_provider_transport(self):
        import frappe
        from vobiz_ai.api import private_recordings
        for method in ['download', 'stream']:
            with patch.object(private_recordings, 'restricted', return_value=True), patch('vobiz_click_to_call.api.recording.' + method) as original:
                with self.assertRaises(frappe.PermissionError):
                    getattr(private_recordings, method).__wrapped__('call')
                original.assert_not_called()

    def test_full_visibility_recordings_keep_original_authorization(self):
        from vobiz_ai.api import private_recordings
        for method in ['download', 'stream']:
            with patch.object(private_recordings, 'restricted', return_value=False), patch('vobiz_click_to_call.api.recording.' + method, return_value='transport') as original:
                self.assertEqual(getattr(private_recordings, method).__wrapped__('call'), 'transport')
                original.assert_called_once_with('call')

    def test_patient_routed_notification_masks_for_target_user(self):
        from vobiz_ai import number_privacy as privacy
        raw = {"call_log": "CALL-1", "patient": "PAT-1",
               "patient_name": "Caller 9876501234", "customer_number": "9876501234",
               "did_number": "18005550100", "agent_phone": "2025550188"}
        with patch.object(privacy, "restricted", return_value=True) as restricted:
            result = privacy.project_patient_routed_notification(raw, "agent@example.test")
        restricted.assert_called_once_with("agent@example.test")
        self.assertNotIn("9876501234", str(result))
        self.assertEqual(result["patient"], "PAT-1")
        self.assertEqual(result["did_number"], "18005550100")
        self.assertEqual(result["agent_phone"], "2025550188")
        self.assertEqual(raw["customer_number"], "9876501234")

    def test_full_view_patient_routed_notification_preserves_contract(self):
        from vobiz_ai import number_privacy as privacy
        raw = {"patient": "PAT-1", "patient_name": "Caller 9876501234",
               "customer_number": "9876501234", "agent_phone": "2025550188"}
        with patch.object(privacy, "restricted", return_value=False) as restricted:
            result = privacy.project_patient_routed_notification(raw, "manager@example.test")
        restricted.assert_called_once_with("manager@example.test")
        self.assertEqual(result, raw)
        self.assertIsNot(result, raw)

    def test_patient_routed_publisher_projects_before_realtime(self):
        from vobiz_ai.api import voice_agent
        context = {"patient": "PAT-1", "display_name": "Caller 9876501234",
                   "phone": "9876501234", "sr_followup_id": "7"}
        routing = {"agent_user": "agent@example.test", "agent_phone": "2025550188",
                   "routing_group": "GROUP-1"}
        safe = {"patient": "PAT-1", "customer_number": "******1234"}
        with patch.object(voice_agent.number_privacy, "project_patient_routed_notification",
                          return_value=safe) as project, \
             patch.object(voice_agent, "_request_value", return_value="CALL-1"), \
             patch.object(voice_agent, "_route_did_from_request", return_value="18005550100"), \
             patch.object(voice_agent.frappe, "publish_realtime") as publish:
            voice_agent._publish_patient_routed_call(context, routing)
        payload, user = project.call_args.args
        self.assertEqual(user, "agent@example.test")
        self.assertEqual(payload["customer_number"], "9876501234")
        publish.assert_called_once_with("vobiz_patient_routed_call", safe,
                                        user="agent@example.test", after_commit=True)
