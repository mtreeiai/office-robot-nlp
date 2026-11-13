FROM ros:humble-ros-base
RUN apt-get update && apt-get install -y --no-install-recommends \
    ros-humble-nav2-msgs ros-humble-nav-msgs python3-colcon-common-extensions \
    && rm -rf /var/lib/apt/lists/*
COPY core /workspace/core
COPY src /workspace/ros_ws/src
RUN . /opt/ros/humble/setup.sh && cd /workspace/ros_ws && \
    colcon build --packages-select robot_interfaces robot_mission_control robot_navigation_manager office_robot_edge_gateway \
    --cmake-args -DBUILD_TESTING=OFF
COPY check_stack.py /workspace/check_stack.py
ENV ROS_LOCALHOST_ONLY=1 ROS_DOMAIN_ID=73
CMD ["bash", "-c", "source /opt/ros/humble/setup.bash && source /workspace/ros_ws/install/setup.bash && export PYTHONPATH=/workspace/core:$PYTHONPATH && python3 /workspace/check_stack.py"]
