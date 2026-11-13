ALTER TABLE locations ADD CONSTRAINT complete_navigation_pose CHECK (
    pose ?& ARRAY['x','y','yaw','frameId']
    AND jsonb_typeof(pose->'x')='number' AND jsonb_typeof(pose->'y')='number'
    AND jsonb_typeof(pose->'yaw')='number' AND jsonb_typeof(pose->'frameId')='string'
    AND length(pose->>'frameId')>0
);
ALTER TABLE delivery_tasks ADD CONSTRAINT queued_task_has_fifo CHECK (state<>'QUEUED' OR queue_order IS NOT NULL);
ALTER TABLE delivery_requests ADD CONSTRAINT confirmed_request_has_timestamp CHECK (state<>'CONFIRMED' OR confirmed_at IS NOT NULL);
ALTER TABLE robot_assignments ADD CONSTRAINT resolved_assignment_is_terminal CHECK (
    resolved_at IS NULL OR state IN ('COMPLETED','FAILED','CANCELLED')
);
ALTER TABLE robot_assignments ADD CONSTRAINT completed_assignment_has_pickup CHECK (
    state<>'COMPLETED' OR (picked_up AND resolved_at IS NOT NULL)
);
