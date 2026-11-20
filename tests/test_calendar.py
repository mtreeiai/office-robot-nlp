"""Calendar tests use ONLY an explicitly selected disposable PostgreSQL database."""
import copy
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
from fastapi.testclient import TestClient

from office_room_calendar.auth import CalendarAuth, HASHER, digest
from office_room_calendar.availability import AvailabilityService, business_windows, first_gap, merge_intervals, vietnamese_feedback
from office_room_calendar.config import CalendarSettings
from office_room_calendar.database import CalendarDatabase
from office_room_calendar.errors import CalendarError
from office_room_calendar.service import CalendarService, utc
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from http_conversation_demo import SINGLE, THREE, STRICT, CONFLICT, UNKNOWN_ROOM, demo_responses
from text_conversation import TextConversationApplication

UTC = timezone.utc
NOW = datetime(2026, 11, 19, 2, 0, tzinfo=UTC)  # Thursday, 09:00 in Vietnam
PASSWORD = 'calendar-only-test-password'
ROBOT_TOKEN = 'robot-test-only-' + 'r' * 40
DB_URL = os.environ.get('CALENDAR_TEST_DATABASE_URL')
SINGLE_01 = 'Giao tài liệu đến phòng 01.'
SAME_ROOM = 'Giao hợp đồng và báo cáo đến phòng 01.'


def execution_responses():
    responses=copy.deepcopy(demo_responses())
    responses[SINGLE_01]=copy.deepcopy(responses[SINGLE])
    responses[SINGLE_01]['parsedIntent']['destinationText']='phòng 01'
    responses[SAME_ROOM]=copy.deepcopy(responses[THREE])
    responses[SAME_ROOM]['tasks']=responses[SAME_ROOM]['tasks'][:2]
    for task in responses[SAME_ROOM]['tasks']: task['destinationText']='phòng 01'
    return responses


class CalendarAlgorithmsTests(unittest.TestCase):
    def test_merge_and_find_gap_across_adjacent_overlapping_intervals(self):
        blocks = merge_intervals([(3,5),(1,3),(4,6),(8,9)])
        self.assertEqual(blocks, [(1,6),(8,9)])
        self.assertEqual(first_gap(blocks, 2, 12, 2), 6)
        self.assertIsNone(first_gap(blocks, 2, 10, 4))

    def test_time_requires_offset_and_normalizes_utc(self):
        self.assertEqual(utc('2026-11-19T09:00:00+07:00'), NOW)
        for value in ['2026-11-19T09:00:00','not a time',None]:
            with self.assertRaises(CalendarError): utc(value)

    def test_imaginary_business_hours_not_granted(self):
        start=utc('2027-03-14T00:00:00-05:00')
        self.assertEqual(list(business_windows(start,start+timedelta(days=1),'America/New_York',{'6':[['02:00','04:00']]})),[])

    def test_configuration_keeps_secrets_private_and_requires_secure_origin(self):
        settings=CalendarSettings('postgresql://private-credentials','s'*40)
        self.assertNotIn('private-credentials',repr(settings))
        self.assertNotIn('s'*40,repr(settings))
        for origin in ['http://example.com','https://example.com/path','https://a:99999','https://name:secret@a']:
            with self.assertRaises(ValueError): replace(settings,public_origin=origin)

    def test_configuration_rejects_render_http_and_shared_secrets(self):
        with patch.dict(os.environ, {'RENDER':'true','CALENDAR_PUBLIC_ORIGIN':'http://127.0.0.1:8000'}):
            with self.assertRaises(ValueError): CalendarSettings.from_environment()
        with patch.dict(os.environ, {'RENDER':'false','API_ACCESS_TOKEN':'s'*40,'CALENDAR_SESSION_SECRET':'s'*40}):
            with self.assertRaises(ValueError): CalendarSettings.from_environment()


@unittest.skipUnless(DB_URL, 'Set CALENDAR_TEST_DATABASE_URL to a disposable calendar_test database')
class CalendarPostgresTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        target=conninfo_to_dict(DB_URL)
        if (target.get('host') not in {'127.0.0.1','localhost','::1'}
                or not all(word in target.get('dbname','').lower() for word in ('calendar','test'))):
            raise RuntimeError('Calendar tests require a loopback, disposable calendar test database')
        cls.password_hash=HASHER.hash(PASSWORD)

    def setUp(self):
        self.network=patch('urllib.request.urlopen',side_effect=AssertionError('Live providers forbidden'))
        self.network.start(); self.addCleanup(self.network.stop)
        self.db=CalendarDatabase(DB_URL,'calendar_test_'+uuid4().hex)
        self.addCleanup(self.drop_schema)
        self.db.migrate({f'{i:02d}':f'Phòng {i}' for i in range(1,8)})
        self.now=NOW
        self.settings=CalendarSettings(DB_URL,'session-test-'+'s'*40)
        self.service=CalendarService(self.db,self.settings,lambda:self.now)
        self.availability=AvailabilityService(self.db,self.settings,lambda:self.now)
        self.auth=CalendarAuth(self.db,self.settings,lambda:self.now)
        self.owner={'userId':str(uuid4()),'displayName':'Owner','role':'USER'}
        self.other={'userId':str(uuid4()),'displayName':'Other','role':'USER'}
        self.admin={'userId':str(uuid4()),'displayName':'Admin','role':'ADMIN'}
        with self.db.transaction() as conn:
            for actor in [self.owner,self.other,self.admin]:
                conn.execute('INSERT INTO calendar_users(user_id,email,display_name,password_hash,role) VALUES (%s,%s,%s,%s,%s)',
                             (actor['userId'],actor['displayName'].lower()+'@test.local',actor['displayName'],self.password_hash,actor['role']))

    def drop_schema(self):
        with psycopg.connect(DB_URL) as conn:
            conn.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(self.db.schema)))

    def configure(self,room='02',**changes):
        current=next(r for r in self.service.rooms() if r['roomId']==room)
        return self.service.configure_room(self.admin,room,changes,current['calendarVersion'])

    def body(self,room='02',start=NOW,end=NOW+timedelta(hours=1),**extra):
        return {'roomId':room,'title':'Team meeting','startAt':start.isoformat(),'endAt':end.isoformat(),'idempotencyKey':str(uuid4()),**extra}

    def book(self,**kwargs):
        return self.service.create_booking(self.owner,self.body(**kwargs))['bookings'][0]

    def check(self,room='02',start=NOW,end=NOW+timedelta(minutes=3),**kwargs):
        return self.availability.check(room,{'start':start.isoformat(),'end':end.isoformat()},**kwargs)

    def assert_code(self,code,callback):
        with self.assertRaises(CalendarError) as result: callback()
        self.assertEqual(result.exception.code,code)

    def make_client(self,nlp=False,responses=None,environment_flags=False):
        registry=load_semantic_registry()
        self.apps={}
        def factory(sid):
            app=TextConversationApplication(FakeLanguageUnderstandingParser(copy.deepcopy(responses or demo_responses())),registry.map_catalog,room_registry=registry,session_id=sid)
            self.apps[sid]=app; return app
        self.bridge=NlpSessionBridge(factory)
        if environment_flags:
            with patch.dict(os.environ,{'RENDER':'false','CALENDAR_ENABLED':'true','CALENDAR_NLP_ENABLED':'true',
                                       'CALENDAR_DATABASE_URL':DB_URL,'CALENDAR_SESSION_SECRET':self.settings.session_secret,
                                       'CALENDAR_PUBLIC_ORIGIN':self.settings.public_origin}), \
                    patch('office_room_calendar.database.CalendarDatabase',return_value=self.db):
                api=create_api(self.bridge,ApiSettings(access_token='backend-private-token'))
        else:
            api=create_api(self.bridge,ApiSettings(access_token='backend-private-token'),self.service,replace(self.settings,nlp_enabled=nlp))
        client=TestClient(api,base_url=self.settings.public_origin)
        client.__enter__(); self.addCleanup(client.__exit__,None,None,None)
        return client

    def login(self,client,email='owner@test.local'):
        result=client.post('/v1/calendar/auth/login',headers={'Origin':self.settings.public_origin},json={'email':email,'password':PASSWORD})
        self.assertEqual(result.status_code,200,result.text)
        return {'Origin':self.settings.public_origin,'X-CSRF-Token':result.json()['csrfToken']}

    def test_seed_safe_defaults_and_migration_idempotency(self):
        self.assertEqual([r['roomId'] for r in self.service.rooms()],[f'{i:02d}' for i in range(1,8)])
        self.assertTrue(all(not r['bookable'] and r['deliveryPolicy']=='BLOCK_WHEN_BUSY' for r in self.service.rooms()))
        self.configure(bookable=True)
        self.db.migrate({f'{i:02d}':'ignored new name' for i in range(1,8)})
        self.assertTrue(self.service.rooms()[1]['bookable'])
        self.assertEqual(self.service.rooms()[1]['displayName'],'Phòng 2')

    def test_booking_requires_admin_bookability_confirmation(self):
        self.assert_code('ROOM_NOT_BOOKABLE',lambda:self.book())
        self.assert_code('FORBIDDEN',lambda:self.service.configure_room(self.owner,'02',{'bookable':True},1))
        self.configure(bookable=True); self.assertEqual(self.book()['status'],'CONFIRMED')

    def test_overlap_rejected_boundaries_and_different_rooms_allowed(self):
        self.configure(bookable=True); self.configure('03',bookable=True); self.book()
        for start,end in [(NOW,NOW+timedelta(minutes=1)),(NOW-timedelta(minutes=1),NOW+timedelta(hours=2)),(NOW+timedelta(minutes=59),NOW+timedelta(hours=2))]:
            self.assert_code('BOOKING_CONFLICT',lambda:self.book(start=start,end=end))
        self.book(start=NOW+timedelta(hours=1),end=NOW+timedelta(hours=2))
        self.book(room='03')

    def test_postgres_constraint_cannot_be_bypassed_by_direct_insert(self):
        self.configure(bookable=True); self.book()
        with self.assertRaises(psycopg.errors.ExclusionViolation):
            with self.db.transaction() as conn:
                conn.execute('INSERT INTO calendar_bookings(booking_id,room_id,organizer_id,title,start_at,end_at) VALUES (%s,%s,%s,%s,%s,%s)',(uuid4(),'02',self.owner['userId'],'raw',NOW,NOW+timedelta(hours=1)))

    def test_concurrent_overlapping_creates_only_one_wins(self):
        self.configure(bookable=True); barrier=Barrier(2)
        def request(_):
            barrier.wait(timeout=5)
            try: return self.book()['status']
            except CalendarError as error: return error.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(request,range(2))),['CONFIRMED','BOOKING_CONFLICT'])
        self.assertEqual(len(self.service.list_bookings(NOW,NOW+timedelta(hours=2))),1)

    def test_concurrent_idempotent_retries_only_create_once(self):
        self.configure(bookable=True); body=self.body(); barrier=Barrier(2)
        def request(_): barrier.wait(timeout=5); return self.service.create_booking(self.owner,body)
        with ThreadPoolExecutor(max_workers=2) as pool: results=list(pool.map(request,range(2)))
        self.assertEqual(results[0],results[1])
        self.assert_code('IDEMPOTENCY_CONFLICT',lambda:self.service.create_booking(self.owner,{**body,'title':'different'}))

    def test_edit_cancel_owner_admin_and_optimistic_concurrency(self):
        self.configure(bookable=True); booking=self.book(); bid=booking['bookingId']
        self.assert_code('FORBIDDEN',lambda:self.service.change_booking(self.other,bid,{'title':'bad'},1))
        self.assert_code('FORBIDDEN',lambda:self.service.change_booking(self.other,bid,{},1,True))
        changed=self.service.change_booking(self.owner,bid,{'title':'updated'},1)
        self.assertEqual(changed['revision'],2)
        self.assert_code('STALE_VERSION',lambda:self.service.change_booking(self.owner,bid,{'title':'stale'},1))
        cancelled=self.service.change_booking(self.admin,bid,{},2,True)
        self.assertEqual(cancelled['status'],'CANCELLED')
        self.assertEqual(self.service.change_booking(self.admin,bid,{},None,True),cancelled)
        self.assert_code('BOOKING_CANCELLED',lambda:self.service.change_booking(self.owner,bid,{'title':'resurrect'},3))
        self.book()  # Cancellation frees the interval.

    def test_conflicting_edit_rolls_back_versions_and_audit(self):
        self.configure(bookable=True); self.book(); second=self.book(start=NOW+timedelta(hours=2),end=NOW+timedelta(hours=3))
        before=self.service.rooms()
        with self.db.transaction(readonly=True) as c: count=c.execute('SELECT count(*) n FROM calendar_audit_logs').fetchone()['n']
        self.assert_code('BOOKING_CONFLICT',lambda:self.service.change_booking(self.owner,second['bookingId'],{'startAt':NOW.isoformat(),'endAt':(NOW+timedelta(hours=1)).isoformat()},1))
        self.assertEqual(self.service.rooms(),before)
        with self.db.transaction(readonly=True) as c: self.assertEqual(c.execute('SELECT count(*) n FROM calendar_audit_logs').fetchone()['n'],count)

    def test_atomic_weekly_series_rolls_back_every_occurrence_on_conflict(self):
        self.configure(bookable=True); self.book(start=NOW+timedelta(weeks=1),end=NOW+timedelta(weeks=1,hours=1))
        self.assert_code('BOOKING_CONFLICT',lambda:self.service.create_booking(self.owner,self.body(recurrence={'weeks':4,'timezone':'Asia/Ho_Chi_Minh'})))
        with self.db.transaction(readonly=True) as conn:
            self.assertEqual(conn.execute('SELECT count(*) n FROM calendar_bookings').fetchone()['n'],1)
            self.assertEqual(conn.execute('SELECT count(*) n FROM calendar_booking_series').fetchone()['n'],0)

    def test_weekly_series_maintains_local_wall_time_across_dst(self):
        self.configure(bookable=True)
        result=self.service.create_booking(self.owner,self.body(start=utc('2026-10-29T09:00:00-04:00'),end=utc('2026-10-29T10:00:00-04:00'),recurrence={'weeks':2,'timezone':'America/New_York'}))
        self.assertTrue(result['seriesId']); self.assertEqual(len(result['bookings']),2)
        starts=[utc(b['startAt']) for b in result['bookings']]
        self.assertEqual(starts[1]-starts[0],timedelta(weeks=1,hours=1))

    def test_naive_invalid_and_excessive_booking_intervals(self):
        self.configure(bookable=True)
        for changes in [{'startAt':'2026-11-19T09:00'},{'endAt':NOW.isoformat()},{'endAt':(NOW+timedelta(days=2)).isoformat()},{'roomId':'room-02'}]:
            with self.assertRaises(CalendarError): self.service.create_booking(self.owner,{**self.body(),**changes})

    def test_free_busy_boundary_future_arrival_and_cancellation(self):
        self.configure(bookable=True); b=self.book(start=NOW+timedelta(minutes=5),end=NOW+timedelta(minutes=10))
        self.assertEqual(self.check()['calendarStatus'],'FREE')
        busy=self.check(start=NOW+timedelta(minutes=6),end=NOW+timedelta(minutes=9))
        self.assertEqual((busy['calendarStatus'],busy['deliveryDecision']),('BUSY','DEFER'))
        self.assertEqual(utc(busy['nextAvailableFrom']),NOW+timedelta(minutes=10))
        self.assertEqual(self.check(start=NOW+timedelta(minutes=10),end=NOW+timedelta(minutes=13))['deliveryDecision'],'ALLOW')
        self.service.change_booking(self.owner,b['bookingId'],{},1,True)
        self.assertEqual(self.check(start=NOW+timedelta(minutes=6),end=NOW+timedelta(minutes=9))['calendarStatus'],'FREE')

    def test_arrival_window_requires_entire_handoff_interval(self):
        self.configure(bookable=True); self.book(start=NOW+timedelta(minutes=2),end=NOW+timedelta(minutes=10))
        result=self.check(start=NOW+timedelta(minutes=1),end=NOW+timedelta(minutes=4))
        self.assertEqual(result['deliveryDecision'],'DEFER')
        self.assertEqual(utc(result['nextAvailableFrom']),NOW+timedelta(minutes=10))

    def test_contiguous_busy_intervals_merge_and_next_gap_fits_duration(self):
        self.configure(bookable=True); self.book(end=NOW+timedelta(minutes=10)); self.book(start=NOW+timedelta(minutes=10),end=NOW+timedelta(minutes=20))
        self.book(start=NOW+timedelta(minutes=22),end=NOW+timedelta(minutes=30))
        result=self.check()
        self.assertEqual(utc(result['busyUntil']),NOW+timedelta(minutes=20))
        self.assertEqual(utc(result['nextAvailableFrom']),NOW+timedelta(minutes=30))

    def test_door_handoff_preserves_busy_status_and_can_allow(self):
        self.configure(bookable=True,deliveryPolicy='ALLOW_DOOR_HANDOFF'); self.book()
        result=self.check()
        self.assertEqual((result['calendarStatus'],result['deliveryDecision'],result['reasonCode']),('BUSY','ALLOW','DOOR_HANDOFF_ALLOWED'))

    def test_disabled_approval_and_room_disabled_are_separate_from_calendar(self):
        for policy,decision in [('DISABLED','DENY'),('MANAGER_APPROVAL','NEEDS_APPROVAL')]:
            self.configure(deliveryPolicy=policy); result=self.check()
            self.assertEqual((result['calendarStatus'],result['deliveryDecision']),('FREE',decision))
        self.configure(enabled=False); result=self.check()
        self.assertEqual((result['calendarStatus'],result['deliveryDecision']),('UNKNOWN','DENY'))

    def test_business_hours_timezone_and_missing_configuration(self):
        self.configure(deliveryPolicy='BUSINESS_HOURS_ONLY')
        self.assertEqual(self.check()['deliveryDecision'],'UNKNOWN')
        self.configure(workingHours={'3':[['10:00','17:00']]})
        result=self.check(); self.assertEqual(result['deliveryDecision'],'DEFER')
        self.assertEqual(utc(result['nextAvailableFrom']),NOW+timedelta(hours=1))
        self.assertEqual(self.check(start=NOW+timedelta(hours=1),end=NOW+timedelta(hours=1,minutes=3))['deliveryDecision'],'ALLOW')

    def test_invalid_policy_hours_and_stale_room_version(self):
        for changes in [{'deliveryPolicy':'llm-allow'},{'workingHours':{'7':[['08:00','17:00']]}},{'workingHours':{'1':[['17:00','08:00']]}},{'timezone':'Mars'}]:
            with self.assertRaises(CalendarError): self.configure(**changes)
        self.configure(bookable=True)
        self.assert_code('STALE_VERSION',lambda:self.service.configure_room(self.admin,'02',{'bookable':False},1))

    def test_adjacent_business_hours_form_one_continuous_window(self):
        self.configure(deliveryPolicy='BUSINESS_HOURS_ONLY',workingHours={'3':[['09:00','10:00'],['10:00','12:00']]})
        result=self.check(start=NOW+timedelta(minutes=50),end=NOW+timedelta(hours=1,minutes=20))
        self.assertEqual(result['deliveryDecision'],'ALLOW')

    def test_dst_imaginary_weekly_booking_rejected_before_any_insert(self):
        self.configure(bookable=True)
        self.assert_code('INVALID_RECURRENCE',lambda:self.service.create_booking(self.owner,self.body(
            start=utc('2027-03-07T02:30:00-05:00'),end=utc('2027-03-07T03:30:00-05:00'),
            recurrence={'weeks':2,'timezone':'America/New_York'})))
        with self.db.transaction(readonly=True) as c:
            self.assertEqual(c.execute('SELECT count(*) n FROM calendar_bookings').fetchone()['n'],0)

    def test_secure_cookie_and_injected_backend_token_cannot_authenticate_calendar(self):
        self.settings=replace(self.settings,public_origin='https://calendar.test')
        client=self.make_client(); result=client.post('/v1/calendar/auth/login',headers={'Origin':self.settings.public_origin},json={'email':'owner@test.local','password':PASSWORD})
        self.assertEqual(result.status_code,200,result.text)
        cookie=result.headers['set-cookie'].lower()
        for attribute in ['secure','httponly','samesite=strict','path=/v1/calendar']:
            self.assertIn(attribute,cookie)
        client.cookies.clear()
        self.assertEqual(client.get('/v1/calendar/rooms',headers={'Authorization':'Bearer backend-private-token'}).status_code,401)

    def test_preliminary_missing_eta_never_grants_allow(self):
        result=self.availability.check('02')
        self.assertEqual((result['calendarStatus'],result['deliveryDecision'],result['checkType']),('FREE','UNKNOWN','PRELIMINARY'))
        self.assertFalse(result['deliveryAllowed']); self.assertEqual(result['checkedAt'],result['validUntil'])
        self.configure(bookable=True); self.book()
        self.assertEqual(self.availability.check('02')['deliveryDecision'],'DEFER')

    def test_expired_window_unknown_and_ttl_version_invalidation(self):
        result=self.check(); self.assertEqual(utc(result['validUntil'])-utc(result['checkedAt']),timedelta(seconds=30))
        self.configure(bookable=True)
        updated=self.check(known_version=result['calendarVersion']); self.assertTrue(updated['calendarChanged'])
        self.assertFalse(self.check(known_version=updated['calendarVersion'])['calendarChanged'])
        self.now+=timedelta(minutes=10)
        self.assertEqual(self.check()['reasonCode'],'WINDOW_EXPIRED')

    def test_database_failure_returns_unknown_without_internal_details(self):
        with patch.object(self.db,'transaction',side_effect=psycopg.OperationalError('secret database URL')):
            result=self.check()
        self.assertEqual((result['calendarStatus'],result['deliveryDecision']),('UNKNOWN','UNKNOWN'))
        self.assertFalse(result['deliveryAllowed']); self.assertIsNone(result['calendarVersion']); self.assertNotIn('secret',str(result))

    def test_restart_retains_bookings_sessions_and_audit(self):
        self.configure(bookable=True); b=self.book(); token,actor=self.auth.login('owner@test.local',PASSWORD,'local')
        restarted=CalendarDatabase(DB_URL,self.db.schema)
        auth=CalendarAuth(restarted,self.settings,lambda:NOW)
        self.assertEqual(auth.user(token),actor)
        service=CalendarService(restarted,self.settings)
        self.assertEqual(service.list_bookings(NOW,NOW+timedelta(hours=2))[0]['bookingId'],b['bookingId'])
        with restarted.transaction(readonly=True) as c:
            self.assertIn('BOOKING_CREATED',[r['action'] for r in c.execute('SELECT action FROM calendar_audit_logs').fetchall()])

    def test_password_and_session_storage_revocation_expiry(self):
        uid=self.auth.create_user('new@test.local','New',PASSWORD)
        token,actor=self.auth.login('new@test.local',PASSWORD,'local')
        with self.db.transaction(readonly=True) as c:
            row=c.execute('SELECT password_hash FROM calendar_users WHERE user_id=%s',(uid,)).fetchone()
            self.assertTrue(row['password_hash'].startswith('$argon2id$')); self.assertNotEqual(row['password_hash'],PASSWORD)
            self.assertEqual(c.execute('SELECT token_hash FROM calendar_sessions').fetchone()['token_hash'],digest(token))
        self.auth.logout(token,actor); self.assert_code('UNAUTHORIZED',lambda:self.auth.user(token))
        token,_=self.auth.login('new@test.local',PASSWORD,'local'); self.now+=timedelta(hours=13)
        self.assert_code('UNAUTHORIZED',lambda:self.auth.user(token))

    def test_login_throttle_is_persistent_and_generic(self):
        for _ in range(8): self.assert_code('INVALID_CREDENTIALS',lambda:self.auth.login('missing@test.local','bad','remote'))
        self.assert_code('LOGIN_THROTTLED',lambda:self.auth.login('missing@test.local','bad','remote'))
        self.now+=timedelta(minutes=16)
        self.assert_code('INVALID_CREDENTIALS',lambda:self.auth.login('missing@test.local','bad','remote'))

    def test_disabled_user_and_robot_tokens_rejected(self):
        token,_=self.auth.login('owner@test.local',PASSWORD,'local'); self.auth.create_robot('test',ROBOT_TOKEN)
        with self.db.transaction() as c:
            c.execute('UPDATE calendar_users SET enabled=false WHERE user_id=%s',(self.owner['userId'],))
            c.execute('UPDATE calendar_robot_tokens SET enabled=false')
        self.assert_code('UNAUTHORIZED',lambda:self.auth.user(token))
        self.assert_code('UNAUTHORIZED',lambda:self.auth.robot(ROBOT_TOKEN))
        self.assert_code('INVALID_TOKEN',lambda:self.auth.create_robot('shared',self.settings.session_secret))

    def test_http_cookie_csrf_origin_and_auth_boundaries(self):
        client=self.make_client()
        self.assertEqual(client.get('/calendar/').status_code,200)
        self.assertEqual(client.get('/v1/calendar/rooms').status_code,401)
        self.assertEqual(client.post('/v1/sessions',json={}).status_code,401)
        self.assertEqual(client.post('/v1/calendar/auth/login',json={'email':'owner@test.local','password':PASSWORD}).status_code,403)
        headers=self.login(client)
        cookie=client.cookies.get('calendar_session'); self.assertTrue(cookie)
        self.assertEqual(client.get('/v1/calendar/rooms').status_code,200)
        self.assertEqual(client.post('/v1/calendar/bookings',json=self.body()).status_code,403)
        self.assertEqual(client.post('/v1/calendar/bookings',headers={**headers,'Origin':'https://evil.example'},json=self.body()).status_code,403)
        self.assertEqual(client.patch('/v1/calendar/rooms/02',headers=headers,json={'expectedVersion':1,'bookable':True}).status_code,403)
        self.assertEqual(client.get('/v1/calendar/audit').status_code,403)
        self.assertEqual(client.post('/v1/calendar/auth/logout',headers=headers).status_code,200)
        self.assertEqual(client.get('/v1/calendar/rooms').status_code,401)

    def test_http_booking_crud_validation_and_conflict_contract(self):
        self.configure(bookable=True); client=self.make_client(); headers=self.login(client)
        body=self.body(); result=client.post('/v1/calendar/bookings',headers=headers,json=body)
        self.assertEqual(result.status_code,201,result.text); bid=result.json()['bookings'][0]['bookingId']
        self.assertEqual(client.post('/v1/calendar/bookings',headers=headers,json=body).json(),result.json())
        conflict=client.post('/v1/calendar/bookings',headers=headers,json=self.body())
        self.assertEqual(conflict.status_code,409); self.assertEqual(conflict.json()['error']['code'],'BOOKING_CONFLICT')
        self.assertEqual(client.patch('/v1/calendar/bookings/'+bid,headers=headers,json={'expectedRevision':1,'title':'Edited'}).status_code,200)
        self.assertEqual(client.delete('/v1/calendar/bookings/'+bid+'?expectedRevision=2',headers=headers).status_code,200)
        self.assertEqual(client.post('/v1/calendar/bookings',headers=headers,json={**self.body(),'role':'ADMIN'}).status_code,422)
        self.assertEqual(client.patch('/v1/calendar/bookings/not-uuid',headers=headers,json={'expectedRevision':1}).status_code,422)

    def test_robot_read_only_auth_and_safe_evidence(self):
        self.auth.create_robot('test',ROBOT_TOKEN); self.configure(bookable=True); self.book(title='Private meeting title')
        client=self.make_client(); self.login(client)
        body={'roomId':'02','arrivalWindow':{'start':NOW.isoformat(),'end':(NOW+timedelta(minutes=3)).isoformat()}}
        path='/v1/robot/availability/check'
        self.assertEqual(client.post(path,json=body).status_code,401)
        self.assertEqual(client.post(path,headers={'Authorization':'Bearer backend-private-token'},json=body).status_code,401)
        robot={'Authorization':'Bearer '+ROBOT_TOKEN}; response=client.post(path,headers=robot,json=body)
        self.assertEqual(response.status_code,200,response.text); self.assertEqual(response.json()['deliveryDecision'],'DEFER')
        self.assertNotIn('Private meeting title',response.text); self.assertNotIn(self.owner['userId'],response.text)
        client.cookies.clear()
        self.assertEqual(client.post('/v1/calendar/bookings',headers=robot,json=self.body()).status_code,401)
        self.assertEqual(client.post('/v1/sessions',headers=robot,json={}).status_code,401)
        self.assertEqual(client.post(path,headers=robot,json={**body,'operation':'MOVE'}).status_code,422)

    def test_http_db_failure_no_false_free_or_details(self):
        client=self.make_client(); self.login(client)
        with patch.object(self.db,'transaction',side_effect=psycopg.OperationalError('postgresql://private')):
            result=client.get('/v1/calendar/rooms')
        self.assertEqual(result.status_code,503,result.text); self.assertNotIn('private',result.text)

    def test_website_assets_security_and_no_backend_secret(self):
        client=self.make_client()
        for path in ['/calendar/','/calendar/app.js','/calendar/app.css','/calendar/vendor/fullcalendar.min.js']:
            result=client.get(path); self.assertEqual(result.status_code,200,path)
            self.assertNotIn('backend-private-token',result.text)
            self.assertIn("script-src 'self'",result.headers['content-security-policy'])
            self.assertEqual(result.headers['x-content-type-options'],'nosniff')

    def test_nlp_flag_off_preserves_exact_legacy_envelope(self):
        client=self.make_client(); headers={'Authorization':'Bearer backend-private-token'}
        sid=client.post('/v1/sessions',json={},headers=headers).json()['sessionId']
        result=client.post(f'/v1/sessions/{sid}/messages',headers=headers,json={'messageId':'one','text':SINGLE})
        self.assertEqual(set(result.json()),{'sessionId','messageId','dialogueStatus','reply','batch'})
        self.assertEqual(result.json()['dialogueStatus'],'TASK_READY')

    def test_nlp_additive_evidence_single_batch_strict_conflict_and_replay(self):
        self.configure(bookable=True); self.book()
        client=self.make_client(nlp=True); headers={'Authorization':'Bearer backend-private-token'}
        for text in [SINGLE,THREE,STRICT,CONFLICT,UNKNOWN_ROOM]:
            sid=client.post('/v1/sessions',json={},headers=headers).json()['sessionId']
            path=f'/v1/sessions/{sid}/messages'; body={'messageId':'one','text':text}
            result=client.post(path,headers=headers,json=body); self.assertEqual(result.status_code,200,result.text)
            payload=result.json(); self.assertIn('roomAvailability',payload)
            self.assertEqual(client.post(path,headers=headers,json=body).json(),payload)
            if payload['batch']:
                self.assertEqual(len(payload['roomAvailability']),len(payload['batch']['tasks']))
                for evidence,task in zip(payload['roomAvailability'],payload['batch']['tasks']):
                    self.assertEqual(evidence['roomId'],task['destinationId']); self.assertFalse(evidence['deliveryAllowed'])
                self.assertIn('kiểm tra sơ bộ',payload['reply'])
                self.assertEqual(len(self.apps[sid].tasks),len(payload['batch']['tasks']))
            else:
                self.assertEqual(payload['roomAvailability'],[])
                self.assertEqual(payload['dialogueStatus'],'CLARIFICATION_REQUIRED')

    def test_nlp_database_outage_still_preserves_task_and_marks_unknown(self):
        client=self.make_client(nlp=True); headers={'Authorization':'Bearer backend-private-token'}
        sid=client.post('/v1/sessions',json={},headers=headers).json()['sessionId']
        with patch.object(self.db,'transaction',side_effect=psycopg.OperationalError('private')):
            result=client.post(f'/v1/sessions/{sid}/messages',headers=headers,json={'messageId':'one','text':SINGLE})
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['dialogueStatus'],'TASK_READY')
        self.assertEqual(result.json()['roomAvailability'][0]['calendarStatus'],'UNKNOWN')
        self.assertFalse(result.json()['executionDecisions'][0]['allowed'])
        self.assertNotIn('private',result.text)

    def nlp_message(self,client,text=SINGLE_01):
        headers={'Authorization':'Bearer backend-private-token'}
        session=client.post('/v1/sessions',headers=headers,json={})
        self.assertEqual(session.status_code,201,session.text)
        sid=session.json()['sessionId']
        result=client.post(f'/v1/sessions/{sid}/messages',headers=headers,json={'messageId':'m1','text':text})
        self.assertEqual(result.status_code,200,result.text)
        return sid,result.json()

    def assert_pending_execution(self,payload):
        tasks=payload['batch']['tasks'] if payload['batch'] else []
        self.assertEqual(payload['executionDecisions'],[
            {'taskId':t['taskId'],'roomId':t['destinationId'],'status':'PENDING_AVAILABILITY_CHECK',
             'allowed':False,'reasonCode':'ARRIVAL_WINDOW_REQUIRED'} for t in tasks])
        self.assertEqual([(e['taskId'],e['roomId']) for e in payload['roomAvailability']],
                         [(t['taskId'],t['destinationId']) for t in tasks])

    def test_execution_single_room_01_real_service_and_environment_flags(self):
        # Exercise the actual factory flags and configuration parser, using only the test DB.
        client=self.make_client(responses=execution_responses(),environment_flags=True)
        self.now=datetime.now(UTC)  # The factory-created service uses a real clock.
        sid,payload=self.nlp_message(client)
        self.assertEqual(payload['dialogueStatus'],'TASK_READY')
        self.assert_pending_execution(payload)
        self.assertEqual(payload['batch']['tasks'][0]['destinationId'],'01')
        advisory=payload['roomAvailability'][0]
        self.assertEqual((advisory['calendarStatus'],advisory['checkType']),('FREE','PRELIMINARY'))
        self.assertFalse(advisory['deliveryAllowed'])
        self.assertEqual(len(self.apps[sid].tasks),1)
        self.assertIn('trước khi thực thi',payload['reply'])

    def test_execution_single_busy_evidence_is_exact_service_output(self):
        self.configure('01',bookable=True); self.book(room='01')
        client=self.make_client(nlp=True,responses=execution_responses()); sid,payload=self.nlp_message(client)
        self.assert_pending_execution(payload)
        expected={'taskId':payload['batch']['tasks'][0]['taskId'],**self.availability.check('01')}
        self.assertEqual(payload['roomAvailability'],[expected])
        self.assertEqual((expected['calendarStatus'],expected['deliveryDecision']),('BUSY','DEFER'))
        self.assertIn('10:00 19/11/2026',payload['reply'])
        self.assertEqual(payload['batch']['tasks'][0]['lifecycle'],'READY')
        self.assertEqual(len(self.apps[sid].tasks),1)

    def test_execution_multi_room_priority_and_strict_order_preserve_original_tasks(self):
        client=self.make_client(nlp=True)
        for text,policy,priorities in [(THREE,'PRIORITY',['NORMAL','NORMAL','HIGH']),
                                       (STRICT,'STRICT_ORDER',['HIGH','NORMAL'])]:
            with self.subTest(policy=policy):
                sid,payload=self.nlp_message(client,text); self.assert_pending_execution(payload)
                tasks=payload['batch']['tasks']
                self.assertEqual(payload['batch']['orderPolicy'],policy)
                self.assertEqual([t['priority'] for t in tasks],priorities)
                self.assertEqual([t['sequenceIndex'] for t in tasks],list(range(len(tasks))))
                self.assertTrue(all(t['constraints']=={'ifBusy':None,'ifClosed':None} for t in tasks))
                self.assertTrue(all(t['lifecycle']=='READY' and t['handoff']=='NOT_SENT' for t in tasks))
                self.assertEqual(len(self.apps[sid].tasks),len(tasks))
                snapshot=client.get('/v1/batches/'+payload['batch']['batchId'],headers={'Authorization':'Bearer backend-private-token'}).json()
                self.assertEqual(snapshot['orderPreview'],payload['batch']['orderPreview'])

    def test_execution_same_room_keeps_separate_task_associations(self):
        client=self.make_client(nlp=True,responses=execution_responses()); _,payload=self.nlp_message(client,SAME_ROOM)
        self.assert_pending_execution(payload)
        self.assertEqual([e['roomId'] for e in payload['executionDecisions']],['01','01'])
        self.assertEqual(len({e['taskId'] for e in payload['executionDecisions']}),2)

    def test_execution_unresolved_conflicting_and_social_turns_have_no_authorization(self):
        responses=execution_responses()
        responses['Unknown single']=copy.deepcopy(responses[SINGLE_01])
        responses['Unknown single']['parsedIntent']['destinationText']='phòng 08'
        client=self.make_client(nlp=True,responses=responses)
        for text in [UNKNOWN_ROOM,CONFLICT,'Chào bạn','Unknown single']:
            with self.subTest(text=text):
                sid,payload=self.nlp_message(client,text)
                self.assertIsNone(payload['batch']); self.assert_pending_execution(payload)
                self.assertFalse(self.apps[sid].tasks)
        # Clarification creates entries only when the entire existing batch becomes ready.
        sid,_=self.nlp_message(client,UNKNOWN_ROOM)
        resolved=client.post(f'/v1/sessions/{sid}/messages',headers={'Authorization':'Bearer backend-private-token'},
                             json={'messageId':'m2','text':'Phòng 5.'}).json()
        self.assertEqual(resolved['dialogueStatus'],'BATCH_READY'); self.assert_pending_execution(resolved)

    def test_execution_replay_remains_exact_after_calendar_change_without_requery(self):
        client=self.make_client(nlp=True,responses=execution_responses()); sid,payload=self.nlp_message(client)
        headers={'Authorization':'Bearer backend-private-token'}; task=payload['batch']['tasks'][0]
        before=client.get('/v1/tasks/'+task['taskId'],headers=headers).json()
        self.configure('01',enabled=False); self.now+=timedelta(minutes=1)
        with patch.object(AvailabilityService,'check',side_effect=AssertionError('Replay must not recheck Calendar')):
            replay=client.post(f'/v1/sessions/{sid}/messages',headers=headers,json={'messageId':'m1','text':SINGLE_01})
        self.assertEqual(replay.json(),payload)
        self.assertEqual(client.get('/v1/tasks/'+task['taskId'],headers=headers).json(),before)
        self.assertEqual(len(self.apps[sid].tasks),1)
        conflict=client.post(f'/v1/sessions/{sid}/messages',headers=headers,json={'messageId':'m1','text':THREE})
        self.assertEqual(conflict.status_code,409)

    def robot_request(self,client,arrival=True,**extra):
        body={'roomId':'01','operation':'DOCUMENT_DELIVERY',**extra}
        if arrival:
            body['arrivalWindow']={'start':NOW.isoformat(),'end':(NOW+timedelta(minutes=3)).isoformat()}
        return client.post('/v1/robot/availability/check',headers={'Authorization':'Bearer '+ROBOT_TOKEN},json=body)

    def test_execution_final_api_free_busy_and_policy_contract_unchanged(self):
        self.auth.create_robot('phase7a-test',ROBOT_TOKEN); self.configure('01',bookable=True)
        client=self.make_client(nlp=True)
        for busy in [False,True]:
            if busy: self.book(room='01')
            for policy in ['BLOCK_WHEN_BUSY','ALLOW_DOOR_HANDOFF','BUSINESS_HOURS_ONLY','MANAGER_APPROVAL','DISABLED']:
                self.configure('01',deliveryPolicy=policy,workingHours={'3':[['08:00','17:00']]})
                with self.subTest(busy=busy,policy=policy):
                    response=self.robot_request(client)
                    self.assertEqual(response.status_code,200,response.text)
                    result=response.json(); self.assertEqual(result,self.check(room='01'))
                    self.assertEqual(result['checkType'],'ARRIVAL_WINDOW')
                    self.assertEqual(result['calendarStatus'],'BUSY' if busy else 'FREE')
                    expected={'MANAGER_APPROVAL':'NEEDS_APPROVAL','DISABLED':'DENY'}.get(policy,
                             'ALLOW' if policy=='ALLOW_DOOR_HANDOFF' or not busy else 'DEFER')
                    self.assertEqual(result['deliveryDecision'],expected)
                    self.assertEqual(result['deliveryAllowed'],expected=='ALLOW')
                    self.assertNotIn('executionDecisions',result)
                    self.assertGreater(utc(result['validUntil']),utc(result['checkedAt']))

    def test_execution_robot_preliminary_all_policies_never_allows(self):
        self.auth.create_robot('phase7a-test',ROBOT_TOKEN); client=self.make_client()
        for policy in ['BLOCK_WHEN_BUSY','ALLOW_DOOR_HANDOFF','BUSINESS_HOURS_ONLY','MANAGER_APPROVAL','DISABLED']:
            self.configure('01',deliveryPolicy=policy,workingHours={'3':[['08:00','17:00']]})
            result=self.robot_request(client,arrival=False)
            self.assertEqual(result.status_code,200,result.text)
            self.assertEqual(result.json()['checkType'],'PRELIMINARY')
            self.assertFalse(result.json()['deliveryAllowed'])

    def test_execution_robot_invalid_revoked_and_database_outage_rejected_safely(self):
        self.auth.create_robot('phase7a-test',ROBOT_TOKEN); client=self.make_client()
        path='/v1/robot/availability/check'; body={'roomId':'01'}
        for token in ['invalid','backend-private-token']:
            self.assertEqual(client.post(path,headers={'Authorization':'Bearer '+token},json=body).status_code,401)
        with patch.object(self.db,'transaction',side_effect=psycopg.OperationalError('private DB detail')):
            failure=self.robot_request(client)
        self.assertEqual(failure.status_code,503); self.assertNotIn('private DB detail',failure.text)
        with patch.object(CalendarAuth,'robot',return_value='validated-test-robot'), \
                patch.object(self.db,'transaction',side_effect=psycopg.OperationalError('private DB detail')):
            unknown=self.robot_request(client)
        self.assertEqual(unknown.status_code,200,unknown.text)
        self.assertEqual(unknown.json()['calendarStatus'],'UNKNOWN'); self.assertFalse(unknown.json()['deliveryAllowed'])
        with self.db.transaction() as c: c.execute('UPDATE calendar_robot_tokens SET enabled=false')
        self.assertEqual(self.robot_request(client).status_code,401)

    def test_execution_robot_invalid_and_expired_windows_never_allow(self):
        self.auth.create_robot('phase7a-test',ROBOT_TOKEN); client=self.make_client()
        for start,end in [('2026-11-19T09:00:00',(NOW+timedelta(minutes=3)).isoformat()),
                          (NOW.isoformat(),NOW.isoformat()),
                          (NOW.isoformat(),(NOW+timedelta(hours=9)).isoformat()),
                          ((NOW+timedelta(days=31)).isoformat(),(NOW+timedelta(days=31,minutes=3)).isoformat())]:
            response=self.robot_request(client,arrival=False,arrivalWindow={'start':start,'end':end})
            self.assertEqual(response.status_code,422,response.text)
        self.now+=timedelta(minutes=10)
        expired=self.robot_request(client)
        self.assertEqual(expired.status_code,200,expired.text)
        self.assertEqual(expired.json()['reasonCode'],'WINDOW_EXPIRED'); self.assertFalse(expired.json()['deliveryAllowed'])
