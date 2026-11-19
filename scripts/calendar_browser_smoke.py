"""Real Chromium + PostgreSQL smoke check. ONLY uses a disposable loopback calendar_test DB.

Run from repository root after installing requirements-calendar-browser.txt and Chromium.
Screenshots go to ignored .venv/calendar-browser/. No external provider/network is used.
"""
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict
import uvicorn
from playwright.sync_api import sync_playwright, expect

from office_room_calendar.auth import CalendarAuth
from office_room_calendar.config import CalendarSettings
from office_room_calendar.database import CalendarDatabase
from office_room_calendar.service import CalendarService
from office_robot_nlp.clu import FakeLanguageUnderstandingParser
from office_robot_nlp.http_api import ApiSettings, create_api
from office_robot_nlp.semantic_map import load_semantic_registry
from office_robot_nlp.session_bridge import NlpSessionBridge
from text_conversation import TextConversationApplication


def main():
    url=os.environ.get('CALENDAR_TEST_DATABASE_URL','')
    target=conninfo_to_dict(url)
    if target.get('host') not in {'127.0.0.1','localhost','::1'} or not all(x in target.get('dbname','') for x in ('calendar','test')):
        raise SystemExit('Set CALENDAR_TEST_DATABASE_URL to a dedicated loopback calendar_test database.')
    database=CalendarDatabase(url,'calendar_test_ui_'+uuid4().hex)
    server=thread=None
    output=Path('.venv/calendar-browser'); output.mkdir(parents=True,exist_ok=True)
    def start_server():
        nonlocal server,thread
        listener=socket.socket(); listener.bind(('127.0.0.1',0)); listener.listen(128)
        settings=CalendarSettings(url,'browser-test-session-'+'s'*40,public_origin=f'http://127.0.0.1:{listener.getsockname()[1]}')
        registry=load_semantic_registry()
        bridge=NlpSessionBridge(lambda sid:TextConversationApplication(FakeLanguageUnderstandingParser({}),registry.map_catalog,room_registry=registry,session_id=sid))
        app=create_api(bridge,ApiSettings(access_token='test-only-backend-secret'),CalendarService(database,settings),settings)
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',access_log=False))
        thread=threading.Thread(target=server.run,kwargs={'sockets':[listener]},daemon=True); thread.start()
        for _ in range(100):
            if server.started: return settings
            if not thread.is_alive(): raise AssertionError('Test server failed')
            time.sleep(.05)
        raise AssertionError('Test server timed out')
    def stop_server():
        if server:
            server.should_exit=True; thread.join(timeout=10)
            if thread.is_alive(): raise AssertionError('Server did not stop')
    password='browser-test-password-only'
    try:
        database.migrate({f'{i:02d}':f'Phòng {i}' for i in range(1,8)})
        settings=start_server(); service=CalendarService(database,settings); auth=CalendarAuth(database,settings)
        auth.create_user('admin@test.local','Calendar Admin',password,'ADMIN')
        auth.create_user('user@test.local','Calendar User',password)
        robot_token='browser-test-robot-'+'r'*40
        auth.create_robot('browser-test',robot_token)
        fake=subprocess.run([sys.executable,'-m','scripts.calendar_fake_robot','--base-url',settings.public_origin,
                             '--room','02','--arrival-start',(datetime.now(timezone.utc)+timedelta(minutes=2)).isoformat()],
                            env={**os.environ,'CALENDAR_ROBOT_TOKEN':robot_token},capture_output=True,text=True,timeout=15)
        assert fake.returncode==0 and 'calendarStatus' in fake.stdout,'Fake robot read client failed'
        assert robot_token not in fake.stdout+fake.stderr,'Robot token must stay private'
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch()
            context=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='Asia/Ho_Chi_Minh',locale='vi-VN')
            page=context.new_page(); errors=[]; page.on('pageerror',lambda error:errors.append(str(error)))
            def login(page,email,origin):
                page.goto(origin+'/calendar/')
                expect(page.locator('#login-page')).to_be_visible()
                page.locator('#login-form [name=email]').fill(email)
                page.locator('#login-form [name=password]').fill(password)
                page.locator('#login-form [type=submit]').click()
                expect(page.locator('.room-card')).to_have_count(7)
            login(page,'admin@test.local',settings.public_origin)
            page.screenshot(path=str(output/'desktop-dashboard.png'),full_page=True)
            page.locator('[data-page=rooms]').click()
            for index in [0,1]:
                page.locator('.management-row').nth(index).get_by_role('button').click()
                page.locator('#room-form [name=bookable]').check()
                page.locator('#room-form [type=submit]').click()
                expect(page.locator('#room-dialog')).not_to_be_visible()
                expect(page.locator('.management-row').nth(index)).to_contain_text('Cho đặt lịch')
            page.locator('[data-page=calendar]').click()
            expect(page.locator('.fc-timeGridWeek-view')).to_be_visible()
            start=datetime.now(timezone.utc)+timedelta(hours=1)
            # Render the date in the browser's explicitly labelled local timezone.
            local_start=page.evaluate('(iso)=>localInput(iso)',start.isoformat())
            local_end=page.evaluate('(iso)=>localInput(iso)',(start+timedelta(minutes=30)).isoformat())
            title='Browser meeting <img src=x onerror=alert(1)>'
            def fill_booking():
                page.locator('#new-booking').click()
                for name,value in [('title',title),('startAt',local_start),('endAt',local_end)]: page.locator(f'#booking-form [name={name}]').fill(value)
                page.locator('#booking-form [name=roomId]').select_option('02')
                page.evaluate('refresh()')  # Background refresh must retain the selected room.
                expect(page.locator('#booking-form [name=roomId]')).to_have_value('02')
                page.locator('#save-booking').click()
            fill_booking(); expect(page.locator('#booking-dialog')).not_to_be_visible()
            expect(page.locator('.fc-event')).to_have_count(1)
            assert page.locator('.fc-event img').count()==0,'Booking title must render as text'
            fill_booking(); expect(page.locator('#booking-dialog [role=alert]')).to_contain_text('đã có lịch')
            page.locator('#booking-dialog [data-close]').first.click()
            page.locator('.fc-event').first.click()
            page.locator('#booking-form [name=title]').fill('Updated browser meeting')
            page.locator('#save-booking').click(); expect(page.locator('#booking-dialog')).not_to_be_visible()
            expect(page.locator('.fc-event').first).to_contain_text('Updated browser meeting')
            page.screenshot(path=str(output/'desktop-calendar.png'),full_page=True)
            # A different user can view a shared meeting but cannot edit/cancel it.
            other=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='Asia/Ho_Chi_Minh')
            user_page=other.new_page(); login(user_page,'user@test.local',settings.public_origin)
            user_page.locator('[data-page=calendar]').click(); user_page.locator('.fc-event').first.click()
            expect(user_page.locator('#save-booking')).to_be_hidden(); expect(user_page.locator('#cancel-booking')).to_be_hidden()
            other.close()
            # Stop the application and create a new app/server. Browser session and booking persist.
            stop_server(); settings=start_server()
            page.goto(settings.public_origin+'/calendar/')
            expect(page.locator('.room-card')).to_have_count(7)
            page.locator('[data-page=calendar]').click()
            expect(page.locator('.fc-event')).to_have_count(1)
            page.locator('.fc-event').first.click()
            page.once('dialog',lambda dialog:dialog.accept()); page.locator('#cancel-booking').click()
            expect(page.locator('#booking-dialog')).not_to_be_visible(); expect(page.locator('.fc-event')).to_have_count(0)
            mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,device_scale_factor=1,timezone_id='Asia/Ho_Chi_Minh')
            mobile_page=mobile.new_page(); login(mobile_page,'admin@test.local',settings.public_origin)
            assert mobile_page.evaluate('document.documentElement.scrollWidth <= innerWidth'),'Mobile horizontal overflow'
            mobile_page.screenshot(path=str(output/'mobile-dashboard.png'),full_page=True)
            mobile_page.locator('[data-page=calendar]').click(); expect(mobile_page.locator('.fc-timeGridDay-view')).to_be_visible()
            mobile_page.screenshot(path=str(output/'mobile-calendar.png'),full_page=True)
            # A failed refresh must remove the old FREE cards.
            page.locator('[data-page=dashboard]').click()
            page.route('**/v1/calendar/rooms',lambda route:route.abort())
            page.locator('#refresh').click()
            expect(page.locator('#room-grid')).to_contain_text('Không xác minh được lịch')
            expect(page.locator('.room-card')).to_have_count(0)
            assert errors==[],f'Unexpected browser exceptions: {errors}'
            browser.close()
        print('PASS: Fake robot HTTP client; Chromium login, admin room confirmation, create/conflict/edit/cancel, owner permissions, XSS text, server restart, desktop/mobile and outage feedback.')
        print('Screenshots: .venv/calendar-browser/')
    finally:
        stop_server()
        with psycopg.connect(url) as connection:
            connection.execute(sql.SQL('DROP SCHEMA IF EXISTS {} CASCADE').format(sql.Identifier(database.schema)))


if __name__=='__main__': main()
