"""Pure contract checks; no PostgreSQL, live provider or robot connection."""
import copy
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from office_robot_nlp.api_dto import BatchDTO, MessageResponse, PreviewDTO, TaskDTO
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from office_room_calendar.availability import vietnamese_feedback
from office_room_calendar.integration import ExecutionDecisionDTO, enrich_message
from http_conversation_demo import SINGLE, demo_responses
from text_conversation import TextConversationApplication


def ready_response(rooms=('01',)):
    tasks = [TaskDTO(taskId=f'task_session_example_{index+1:03d}', sequenceIndex=index,
                     documentText='tài liệu', documentId=None, recipientText=None,
                     destinationText='phòng '+room, destinationId=room, priority='NORMAL',
                     constraints={'ifBusy': None, 'ifClosed': None}, lifecycle='READY',
                     handoff='NOT_SENT', documentLoad='UNCONFIRMED') for index, room in enumerate(rooms)]
    return MessageResponse(sessionId='session_example', messageId='m1',
                           dialogueStatus='TASK_READY' if len(tasks)==1 else 'BATCH_READY', reply='Đã ghi nhận.',
                           batch=BatchDTO(batchId=None if len(tasks)==1 else 'batch_example_001',
                                          orderPolicy=None if len(tasks)==1 else 'PRIORITY', tasks=tasks,
                                          orderPreview=[PreviewDTO(position=i, taskId=t.taskId) for i,t in enumerate(tasks)]))


def evidence(room='01', status='FREE', decision='UNKNOWN', reason='ETA_NOT_PROVIDED'):
    return {'roomId':room, 'calendarStatus':status, 'deliveryDecision':decision,
            'deliveryAllowed':decision=='ALLOW', 'deliveryPolicy':'BLOCK_WHEN_BUSY', 'reasonCode':reason,
            'busyUntil':None, 'nextAvailableFrom':None, 'checkedAt':'2026-11-23T07:25:00+00:00',
            'validUntil':'2026-11-23T07:25:00+00:00', 'calendarVersion':1, 'calendarChanged':False,
            'dataSource':'CALENDAR', 'checkType':'PRELIMINARY',
            'arrivalWindow':{'start':'2026-11-23T07:32:00+00:00','end':'2026-11-23T07:35:00+00:00'}}


class CalendarExecutionContractTests(unittest.TestCase):
    def test_stage_stays_pending_even_if_advisory_provider_returns_allow(self):
        source=ready_response(); snapshot=source.model_dump()
        calendar=Mock(); calendar.check.return_value=evidence(decision='ALLOW',reason='CALENDAR_FREE')
        response=enrich_message(calendar,'Asia/Ho_Chi_Minh')(source)
        self.assertEqual(response.executionDecisions[0].model_dump(),{
            'taskId':source.batch.tasks[0].taskId,'roomId':'01','status':'PENDING_AVAILABILITY_CHECK',
            'allowed':False,'reasonCode':'ARRIVAL_WINDOW_REQUIRED'})
        self.assertTrue(response.roomAvailability[0]['deliveryAllowed'])
        self.assertEqual(source.model_dump(),snapshot)  # No input/task mutation.
        self.assertEqual(response.batch,source.batch)
        calendar.check.assert_called_once_with('01')

    def test_execution_stage_dto_rejects_authorized_or_final_states(self):
        for override in [{'allowed':True},{'status':'APPROVED'},{'reasonCode':'CALENDAR_FREE'}]:
            with self.subTest(override=override),self.assertRaises(ValidationError):
                ExecutionDecisionDTO(taskId='existing-task',roomId='01',**override)

    def test_batch_same_room_preserves_each_existing_task_and_envelope(self):
        source=ready_response(('01','01','07')); calendar=Mock()
        source.batch.orderPolicy='STRICT_ORDER'
        source.batch.tasks[0].priority='HIGH'
        source.batch.tasks[0].constraints.ifBusy='WAIT'
        source.batch.tasks[0].constraints.ifClosed='RETRY'
        calendar.check.side_effect=lambda room:evidence(room)
        response=enrich_message(calendar,'Asia/Ho_Chi_Minh')(source)
        self.assertEqual(response.batch.model_dump(),source.batch.model_dump())
        self.assertEqual([d.taskId for d in response.executionDecisions],[t.taskId for t in source.batch.tasks])
        self.assertEqual([d.roomId for d in response.executionDecisions],['01','01','07'])
        self.assertEqual(calendar.check.call_count,3)

    def test_all_non_ready_envelopes_have_empty_arrays_without_calendar_queries(self):
        for status in ['CLARIFICATION_REQUIRED','DIALOGUE_ERROR','UNSUPPORTED','SOCIAL',
                       'TASK_UPDATE_ACCEPTED','PENDING_CANCELLED','DECISION_READY']:
            with self.subTest(status=status):
                source=MessageResponse(sessionId='session_example',messageId='m1',dialogueStatus=status,reply='Existing reply',batch=None)
                calendar=Mock(); result=enrich_message(calendar,'Asia/Ho_Chi_Minh')(source)
                self.assertEqual(result.executionDecisions,[]); self.assertEqual(result.roomAvailability,[])
                self.assertEqual(result.reply,source.reply); calendar.check.assert_not_called()

    def test_feedback_disabled_room_says_denied_and_requires_real_window(self):
        data=evidence(status='UNKNOWN',decision='DENY',reason='ROOM_DISABLED')
        text=vietnamese_feedback(data)
        self.assertIn('không cho phép giao',text)
        self.assertIn('khoảng đến và bàn giao thực tế',text)

    def test_feedback_unknown_policy_and_approval_do_not_imply_permission(self):
        for status,decision,reason,expected in [
            ('UNKNOWN','UNKNOWN','DATABASE_UNAVAILABLE','Chưa xác minh'),
            ('FREE','UNKNOWN','WORKING_HOURS_NOT_CONFIGURED','Chưa xác minh'),
            ('FREE','NEEDS_APPROVAL','MANAGER_APPROVAL_REQUIRED','chưa có xác nhận phê duyệt')]:
            with self.subTest(reason=reason):
                text=vietnamese_feedback(evidence(status=status,decision=decision,reason=reason))
                self.assertIn(expected,text); self.assertIn('trước khi thực thi',text)

    def test_busy_feedback_converts_display_to_vietnamese_time_without_changing_evidence(self):
        data=evidence(status='BUSY',decision='DEFER',reason='ROOM_BOOKED')
        data.update(busyUntil='2026-11-23T09:00:00+00:00',nextAvailableFrom='2026-11-23T09:00:00+00:00')
        original=copy.deepcopy(data); text=vietnamese_feedback(data,'Asia/Ho_Chi_Minh')
        self.assertIn('16:00 23/11/2026',text); self.assertEqual(data,original)

    def test_both_calendar_flags_disabled_preserve_legacy_http_response(self):
        registry=load_semantic_registry()
        bridge=NlpSessionBridge(lambda sid:TextConversationApplication(
            FakeLanguageUnderstandingParser(demo_responses()),registry.map_catalog,room_registry=registry,session_id=sid))
        with patch.dict('os.environ',{'CALENDAR_ENABLED':'false','CALENDAR_NLP_ENABLED':'false','RENDER':'false'}), \
                patch('urllib.request.urlopen',side_effect=AssertionError('Live provider forbidden')), \
                TestClient(create_api(bridge,ApiSettings())) as client:
            sid=client.post('/v1/sessions',json={}).json()['sessionId']
            result=client.post(f'/v1/sessions/{sid}/messages',json={'messageId':'m1','text':SINGLE})
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(set(result.json()),{'sessionId','messageId','dialogueStatus','reply','batch'})
            self.assertEqual(result.json()['dialogueStatus'],'TASK_READY')
