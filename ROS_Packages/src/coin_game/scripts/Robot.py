#!/usr/bin/env python

import math
import random
import rospy
import roslib
from roslib import packages
from coin_game_msgs.srv import *
from RunData import RunData
#-------added by Mahi------------
from franticness import Franticness
from threading import Thread
import Orange
import Orange.feature
import Orange.classification
import Orange.data
import Orange.utils
import Orange.wrappers
import pickle
import datetime
import numpy as np
#--------------------------------
import tf
from tf.transformations import quaternion_from_euler, euler_from_quaternion
from geometry_msgs.msg import *
import actionlib
from actionlib_msgs.msg import *
from std_srvs.srv import *
from move_base_msgs.msg import *
from visualization_msgs.msg import *
from std_msgs.msg import Time

__author__ = 'matthew'


class Robot():

    def __init__(self):

        self.rgb = {"miranda": [1.0, 0.0, 0.0],
                    "prospero": [0.3, 0.3, 1.0],
                    "ferdinand": [0.0, 1.0, 0.0],
                    "ariel": [1.0, 0.843137255, 0.0],
                    "caliban": [1.0, 0.5, 0.0],
                    "trinculo": [0.541176471, 0.168627451, 0.88627451]
                    }

        self.maze_path_points = []
        self.time_between_points = []
        self.read_points()
        self.started = False

        self.collected_coins = 0
        self.health = 100.0
        self.isAlive = True
        self.current_disparity = 0.0
        self.reached_goal = True
        self.marked_time = True
        self.current_arrival_delay = 0.0
        self.exp_goal_arrival_delay = 0.0
        self.current_goal_start_time = rospy.Time(0)

        self.run_data = RunData()
        self.listener = tf.TransformListener()

        self.eval_trust = rospy.ServiceProxy("evaluate_trust", TrustEvaluation)
        self.get_points_at_indices = rospy.ServiceProxy("points_for_indices", PathPointsForIndices)

        # PointA is the INDEX of first point of the path segment the robot is on and PointB is the second
        self.pointA_index = 0
        self.pointB_index = 1

        self.cur_position = Point()
        self.prev_position_index = 0
        self.prev_position = Point()
        self.cur_goal = Point()
        self.cur_coin_location = Point()

        self.num_trusted_goals = 0
        self.num_untrusted_goals = 0

        self.coin_positions = []  # Stores the position of the coins as they are spawned
        self.robot_start_position_for_last_command = Point()

        if rospy.has_param("~name"):
            self.name = rospy.get_param("~name")
        else:
            self.name = "default_robot"

        print "Debug[M] @Robot.py ", self.name, " in Game!"
        # Configure the first starting point
        robot_staring_positions = {
            "trinculo": Point(6.73, 6.0, 0),
            "miranda": Point(6.73, 2.8, 0),
            "ferdinand": Point(2.1, 6.0, 0),
            "prospero": Point(2.1, 2.8, 0),
            "default_name": Point(0, 0, 0)
        }

        self.robot_start_position_for_last_command = robot_staring_positions.get(self.name)

        if rospy.has_param("coin_game/path_tolerance"):
            self.path_tolerance = rospy.get_param("coin_game/path_tolerance")
        else:
            self.path_tolerance = .05

        if rospy.has_param("coin_game/export_dir"):
            self.export_dir = rospy.get_param("coin_game/export_dir")
        else:
            self.export_dir = roslib.packages.get_pkg_subdir("coin_game", "export")

        #-------------added by Mahi------------------------------------------------
        print "Thread for franticness count!!!"
        self.timestamp_h_m_s = "0_0_0"
        self.franticness = Franticness(self.name)
        self.t = Thread(target=self.franticness.mouse_events)
        self.t.start()
        self.export_file = self.export_dir + "/" + self.name + "_" + str(rospy.Time.now().to_sec()) + ".csv"
        with open(self.export_file, 'w') as csvfile:
            csvfile.write("clicks,mouse movements,decision intervals,Cognitive Stress Prediction(rf),h_m_s\n")

        # marking robot's mode of navigation
        self.is_autonomous = {
            "trinculo": False,
            "miranda": False,
            "ferdinand": False,
            "prospero": False,
            "default_name": False
        }
        self.last_attention_time = rospy.get_time() # secs

        #--------------------------------------------------------------------------
        # self.export_file = open(self.export_dir + "/" + self.name + "_" + str(rospy.Time.now().to_sec()) + ".csv", mode='w')
        # self.csv_writer = csv.writer(self.export_file)
        # self.csv_writer.writerow(['Trusted?',
        #                           'Disparity',
        #                           'Health',
        #                           'Time Remaining',
        #                           'Percent Trust',
        #                           'Collected Coins',
        #                           'Coin X',
        #                           'Coin Y',
        #                           'RobotStart X',
        #                           'RobotStart Y',
        #                           #------added by Mahi------------
        #                           'clicks',
        #                           'mouse movements',
        #                           'decision intervals',
        #                           'Cognitive Stress Prediction(rf)',
        #                           'h:m:s'])
        #                           #-------------------------------

        if rospy.has_param("/" + self.name + "/move_base/DWAPlannerROS/max_trans_vel"):
            self.max_trans_vel = rospy.get_param("/" + self.name + "/move_base/DWAPlannerROS/max_trans_vel")
        else:
            self.max_trans_vel = .5

        rospy.loginfo("Waiting for add_robot service...")
        rospy.wait_for_service("/add_robot", 10)
        rospy.loginfo("Done!")

        self.new_goal_listener = rospy.Subscriber("/" + self.name + "/move_base/current_goal", PoseStamped, self.new_goal_callback)
        self.user_activity_listener = rospy.Subscriber('/coin_game/observed_user_action', Time, self.observed_user_action_callback)
        self.user_activity_publisher = rospy.Publisher("/coin_game/observed_user_action", Time, queue_size=3)

        self.time = Marker()
        self.time_publisher = rospy.Publisher("/visualization_marker", Marker, queue_size=10)
        self.time_remaining = 120
        self.add_time()

        try:
            add_robot = rospy.ServiceProxy("/add_robot", RobotNamed)
            add_robot(self.name)
            print "Trying to add {} to the game...".format(self.name)
        except rospy.ServiceException, e:
            print "Service call to add_robot failed: {}".format(e)

        self.spawn_new_coin_service = rospy.ServiceProxy("spawn_new_coin", SpawnNewCoin)
        self.spawn_new_coin()

        self.call_remove_coin_service = rospy.ServiceProxy("remove_coin", RobotNamed)

        self.t = rospy.Timer(rospy.Duration(1), self.update_timer)
        self.start_timer_service = rospy.Service("/" + self.name + "/start_timer", Empty, self.start_timer)

        rospy.on_shutdown(self.cleanup)

        while not rospy.is_shutdown() and self.isAlive:
            self.check_distance()
            rate = rospy.Rate(1.0)
            rate.sleep()

    def start_timer(self, req):
        #--------added by Mahi------
        print "Debug[M] timer"
        self.franticness.error_correction = 0.
        self.franticness.franticness = 0.
        self.franticness._time_arrival_waypoints = rospy.Time.now().to_sec()
        self.franticness._time_newgoal_after_arrival = rospy.Time.now().to_sec()
        self.last_attention_time = rospy.get_time()
        #---------------------------
        self.started = True
        print "{} timer started!".format(self.name)
        return EmptyResponse()

    def read_points(self):
        points_file = open(roslib.packages.get_pkg_subdir("coin_game", "include") + "/points.txt")
        print "Reading points file..."
        i = 0
        for line in points_file:
            split = line.split("  ")
            #print split
            to_add = Point()
            to_add.x = float(split[0])
            to_add.y = float(split[1])
            to_add.z = float(split[2])
            self.maze_path_points.append(to_add)
            i += 1

        points_file.close()

        times_file = open(roslib.packages.get_pkg_subdir("coin_game", "include") + "/time_between_goals.txt")
        print "Reading times file..."
        i = 0

        for line in times_file:
            self.time_between_points.append(float(line))

    def new_goal_callback(self, data):
        goal_is_from_rviz = True
        for p in self.maze_path_points:
            if np.isclose(p.x, data.pose.position.x) and np.isclose(p.y, data.pose.position.y):
                goal_is_from_rviz = False
                break

        if goal_is_from_rviz and self.is_autonomous[self.name]:
            self.is_autonomous[self.name] = False

        if goal_is_from_rviz:
            print "{} goal is from Rviz so should go manul :/"
            self.is_autonomous[self.name] = False
            self.last_attention_time = rospy.get_time() # secs
            self.franticness.error_correction = 0.
            self.franticness.franticness = 0.
            self.franticness._time_arrival_waypoints = rospy.Time.now().to_sec()
            self.franticness._time_newgoal_after_arrival = rospy.Time.now().to_sec()

            #-------------added by Matthew------------------------------------------------
            #if the goal came form RViz, the user sent the goal, report this activity
            time = Time()
            time.data = rospy.get_time()
            self.user_activity_publisher.publish(time)
            #-----------------------------------------------------------------------------
        # Update robot_start_position_for_last_command
        self.robot_start_position_for_last_command = data.pose.position

        self.prev_position_index = self.pointA_index
        self.prev_position = self.cur_position
        closest_known_point = self.maze_path_points[0]
        shortest_distance = sys.maxint

        if self.euclidean_distance(self.cur_position, data.pose.position) <= .2:
            print "Goal too small."
            return

        point_index = 0
        for cur_index in range(0, len(self.maze_path_points)):

            test_distance = self.euclidean_distance(data.pose.position, self.maze_path_points[cur_index])

            if test_distance < shortest_distance:
                point_index = cur_index
                closest_known_point = self.maze_path_points[cur_index]
                shortest_distance = test_distance

        # Make sure we are not out of bounds
        if point_index >= len(self.maze_path_points):
            point_index = len(self.maze_path_points) - 1

        self.pointA_index = point_index
        self.pointB_index = point_index + 1

        # This is the goal that the user gave NOT the goal the robot knows about (this is for arrival detection and time delay calculations)
        self.cur_goal = data.pose.position
        self.reached_goal = False
        self.current_disparity = shortest_distance

        # -----------added by Mahi------------------
        # ignorance time only counts once for the the a given goal after arrival to that goal.
        if self.franticness._is_first_goal_after_arrival:
            self.franticness._time_newgoal_after_arrival = rospy.Time.now().to_sec()
            self.franticness._is_first_goal_after_arrival = False
        else:
            pass
        # ------------------------------------------
        # Check distance now that we have updated everything for this new goal
        self.marked_time = False
        self.check_distance()

    def euclidean_distance(self, p1, p2):
        return math.sqrt(math.pow(p1.x - p2.x, 2) + math.pow(p1.y - p2.y, 2))

    def observed_user_action_callback(self, msg):
        print("Observed user action @ time: ", msg.data)

    # -----------added by Mahi------------------
    def set_autonomous_goal_and_move(self):
        """
        Robot has no idea about where the next coin has popped up. And autonomously it can either move forward or back.
        It seems fair to assume for the robot that coins shows up in maze uniformly thus the robot can keep a history of
        locations of coins and choose either direction based on the less navigated way points.
        """
        # set autonomous goal
        # determine current line segment where the robot is first
        # print "Debug[M] @Robot.py set_autonomous_goal_and_move !!!!"

        def find_index_in_waypoints(p0, path_points):
            closest_ind = 0
            opt_d = np.inf
            for i in range(len(path_points) - 1):
                p1 = path_points[i]
                p2 = path_points[i+1]
                # projection of vector(p0, p1) on vector(p1,p1)
                # http://mathworld.wolfram.com/Point-LineDistance2-Dimensional.html
                # d = np.fabs((p2.x - p1.x)*(p1.y-p0.y) - (p1.x-p0.x)*(p2.y-p1.y)) / np.sqrt((p2.x-p1.x)**2+(p2.y-p1.y)**2)
                if np.isclose(p1.y, p2.y): # horizontal seg so vertical distance
                    if min(p1.x, p2.x)-.2 <= p0.x <= max(p1.x, p2.x)+.2:
                        d = np.fabs(p0.y - p1.y)
                    else:
                        d = np.inf
                if np.isclose(p1.x, p2.x): # vertical seg so horizontal distance
                    if min(p1.y, p2.y)-.2 <= p0.y <= max(p1.y, p2.y)+.2:
                        d = np.fabs(p0.x - p1.x)
                    else:
                        d = np.inf
                if opt_d > d:
                    opt_d = d
                    closest_ind = i
            return closest_ind, opt_d

        cur_ind, dddd = find_index_in_waypoints(self.cur_position, self.maze_path_points)
        if self.euclidean_distance(self.maze_path_points[cur_ind+1], self.cur_position) <= .2:
            cur_ind += 1
        if cur_ind < 13:
            delta_ind = 1
        else:
            delta_ind = -1
        goal_ind = cur_ind + delta_ind
        rospy.loginfo("Debug[M] @Robot.py Autonomous: current between ind {} and {} goal {} curx={} cury={} d={}".
                      format(cur_ind, cur_ind+1, goal_ind, self.cur_position.x, self.cur_position.y, dddd))
        forward_orientations = np.array([1., 1.5, 0., .5, 0, 1.5, 1., .5, 1., 1.5, 1., .5, 1., .5, 0, .5, 1., 1.5, 0, 1.5, 1., 1.5, 0, .5, 0, 1.5, 1., .5, 1., 1.5, 1., 1.]) * np.pi
        backward_orientations = np.array([0.,  0.,.5,  1.,1.5, 1., .5,  0,1.5,  0.,1.5, 0., 1.5, 0, 1.5, 1., 1.5, 0., .5, 1., .5, 0., .5, 1., 1.5, 1., .5, 0., 1.5, 0, .5, 0.]) * np.pi

        if delta_ind == 1:
            goal_location = Pose(self.maze_path_points[goal_ind], Quaternion(*quaternion_from_euler(0, 0, forward_orientations[goal_ind])))
        else:
            goal_location = Pose(self.maze_path_points[goal_ind], Quaternion(*quaternion_from_euler(0, 0, backward_orientations[goal_ind])))

        move_base = actionlib.SimpleActionClient("/" + self.name + "/move_base", MoveBaseAction)

        while not move_base.wait_for_server(rospy.Duration(5)):
            print "Debug[M] @Robot.py Autonomous navigator Waiting for action server...", self.name

        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = "map"
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose = goal_location

        print "Debug[M] @Robot.py Autonomously Sending " + self.name
        move_base.send_goal_and_wait(goal, preempt_timeout=rospy.Duration(0))
        # move_base.send_goal(goal)
        # move_base.stop_tracking_goal()
    # ------------------------------------------

    def check_distance(self):

        if not self.started:
            return

        try:
            self.listener.waitForTransform("/" + self.name + "/base_link", "/map", rospy.Time(0), rospy.Duration(120))
        except tf.Exception:
            rospy.logwarn("Transform lookup timed out for " + self.name + ".")
            self.remove_coin()

        (trans, rot) = self.listener.lookupTransform("/map", "/" + self.name + "/base_link", rospy.Time(0))

        self.cur_position = Point(trans[0], trans[1], trans[2])

        self.determine_closest_path_indices()

        # Are we going through a wall?
        if self.distance_from_path(self.pointA_index, trans) > self.path_tolerance:
            self.health -= 1
            # print "Ouch! Health: " + str(self.health)

        # Compute expected time to get from Point A to Point B, record current_goal_start_time once the robot has started
        # to move toward it's next goal

        if self.marked_time is False and self.euclidean_distance(self.cur_position, self.prev_position) > 0.2:
            # self.current_goal_start_time = rospy.Time.now()
            self.current_goal_start_time = 120 - (12 * self.collected_coins)
            # We must use cur_goal (the point that the user gave) to be able to know when the user has actually arrived
            # self.exp_goal_arrival_delay = (self.euclidean_distance(self.cur_position, self.cur_goal) / self.max_trans_vel) + 1
            self.marked_time = True
            print "Marked Time!"

        # Have we reached our goal?
        if self.reached_goal is False and self.euclidean_distance(self.cur_position, self.cur_goal) <= 0.2:
            # self.current_arrival_delay = (rospy.Time.now() - self.current_goal_start_time).to_sec() - self.exp_goal_arrival_delay
            self.current_arrival_delay = self.time_remaining
            self.reached_goal = True
            print "Yay! Arrival Delay was " + str(self.current_arrival_delay)
            if not self.is_autonomous[self.name]:
                self.evaluate_trust()
            # --------added by Mahi---------
            self.franticness._time_arrival_waypoints = rospy.Time.now().to_sec()
            self.franticness._is_first_goal_after_arrival = True
            self.franticness.error_correction = 0.
            self.franticness.franticness = 0.
            if self.is_autonomous[self.name]:
                print "Call 1"
                self.set_autonomous_goal_and_move()
            # ------------------------------
        # --------added by Mahi-------------
        print "####---------->>> {}".format(rospy.get_time() - self.last_attention_time)
        if rospy.get_time() - self.last_attention_time > 20.:
            # self.last_attention_time = rospy.get_time()
            if not self.is_autonomous[self.name]:
                print "Call 2"
                self.evaluate_trust()
                self.set_autonomous_goal_and_move()
            self.is_autonomous[self.name] = True
        # ----------------------------------

        # Have we collected the coin?
        if self.euclidean_distance(self.cur_position, self.cur_coin_location) <= 0.2:
            print "Collected Coin!"
            self.collected_coins += 1
            if self.health + 10 > 100:
                self.health = 100
            else:
                self.health += 10
            # Remove old coin and spawn a new one
            self.remove_coin()
            self.spawn_new_coin()
            # Reset the timer
            #self.time_remaining = 120 - (12 * self.collected_coins)
            if self.collected_coins <= 5:
                self.time_remaining = 120 - (12 * self.collected_coins)
            else:
                self.time_remaining = 60

        # See if we have won or lost for this robot
        if self.health <= 0 or self.time_remaining <= 0:
            if self.t.is_alive():
                self.t.shutdown()
            self.robot_death_handler()
        elif self.collected_coins == 5:
            if self.t.is_alive():
                self.t.shutdown()
            self.robot_win_handler()

    def evaluate_trust(self):
        try:
            result = self.eval_trust(self.current_disparity, float((100.0 - self.health) / 100.0), self.current_arrival_delay)
            if result.trust is True:
                self.num_trusted_goals += 1.0
            else:
                self.num_untrusted_goals += 1.0
            percent_trust = float(self.num_trusted_goals / (self.num_trusted_goals + self.num_untrusted_goals))
            print "Percent trust so far: " + str(percent_trust)

            # Record result for this command
            self.run_data.add_result(trust_result=result.trust,
                                     disparity=self.current_disparity,
                                     health=self.health,
                                     delay=self.current_arrival_delay,
                                     percent_trust=percent_trust,
                                     collected_coins=self.collected_coins,
                                     coin_position=self.cur_coin_location,
                                     robot_start_position=self.robot_start_position_for_last_command
                                     )
        except rospy.ServiceException, e:
            print "Could not evaluate trust! {}".format(e)

        entry = self.run_data.get_entry_at_index(self.run_data.get_num_instances()-1)
        #------------------added by Mahi---------------------------------------------
        classifier_file = open(roslib.packages.get_pkg_subdir("coin_game", "include") + "/random_forest.pck")
        random_forest_model = pickle.load(classifier_file)
        domain = random_forest_model.domain

        test_data = Orange.data.Instance(domain, [self.franticness.error_correction, self.franticness.franticness, self.franticness.decision_intervals, None])
        cognitive_load_estimate = random_forest_model(test_data)
        classifier_file.close()
        self.timestamp_h_m_s = datetime.datetime.now()
        #----------------------------------------------------------------------------
        # Write the data to the csv file
        # self.csv_writer.writerow([entry.get("trust_result"),
        #                           entry.get("disparity"),
        #                           entry.get("health"),
        #                           entry.get("delay"),
        #                           entry.get("percent_trust"),
        #                           entry.get("collected_coins"),
        #                           entry.get("coin_position").x,
        #                           entry.get("coin_position").y,
        #                           entry.get("robot_start_position").x,
        #                           entry.get("robot_start_position").y,
        #                           #-----------added by Mahi---------------
        #                           self.franticness.error_correction,
        #                           self.franticness.franticness,
        #                           self.franticness.decision_intervals,
        #                           cognitive_load_estimate,
        #                           "{}:{}:{}".format(self.timestamp_h_m_s.hour,self.timestamp_h_m_s.minute, self.timestamp_h_m_s.second)
        #                           ]
        #                           #---------------------------------------
        #                          )
        with open(self.export_file, 'a') as csvfile:
            csvfile.write("{},{},{},{},{}_{}_{}-{}_{}_{}\n".format(self.franticness.error_correction,
                                                             self.franticness.franticness,
                                                             self.franticness.decision_intervals,
                                                             cognitive_load_estimate,self.timestamp_h_m_s.year,self.timestamp_h_m_s.month,self.timestamp_h_m_s.day,
                                                             self.timestamp_h_m_s.hour,self.timestamp_h_m_s.minute, self.timestamp_h_m_s.second))
        # ------------------added by Mahi---------------------------------------------
        print "err={} fran={} dec_intvl={} t_newg={} t_arrival={}, COGLE={}".format(
            self.franticness.error_correction,
            self.franticness.franticness,
            self.franticness.decision_intervals,
            self.franticness._time_newgoal_after_arrival,
            self.franticness._time_arrival_waypoints,
            cognitive_load_estimate
        )

        if cognitive_load_estimate < -.55 and np.fabs(self.franticness.error_correction) > 0.0001 \
                and np.fabs(self.franticness.franticness) > 0.0001\
                and np.fabs(self.franticness.decision_intervals) > 0.0001:
            print "{}(( Autonmous mode activated !!!!!!!!!!!!!!!".format(self.name)
            self.is_autonomous[self.name] = True
        else:
            print "{} Normal Mode activated      ---------------".format(self.name)
            self.is_autonomous[self.name] = False
        # ----------------------------------------------------------------------------

    def determine_closest_path_indices(self):

        cur_closest_index = 0
        cur_shortest_distance = sys.maxint

        for i in range(0, len(self.maze_path_points)):

            test_distance = self.euclidean_distance(self.maze_path_points[i], self.cur_position)

            if test_distance < cur_shortest_distance:
                cur_closest_index = i
                cur_shortest_distance = test_distance

        if cur_closest_index == 0:
            self.pointA_index = 0
            self.pointB_index = 1
        elif cur_closest_index == len(self.maze_path_points) - 1:
            self.pointA_index = len(self.maze_path_points) - 1
            self.pointB_index = self.pointA_index - 1
        else:
            if self.distance_from_path(cur_closest_index, [self.cur_position.x, self.cur_position.y, self.cur_position.z]) <= self.distance_from_path(cur_closest_index + 1,[self.cur_position.x, self.cur_position.y, self.cur_position.z]):
                self.pointA_index = cur_closest_index
                self.pointB_index = cur_closest_index + 1
            else:
                self.pointA_index = cur_closest_index + 1
                self.pointB_index = cur_closest_index + 2

    def distance_from_path(self, i, trans):
        p1 = Point()
        p2 = Point()
        p3 = Point()

        distance = 0.0
        numerator = 0.0
        denominator = 0.0

        if i <= 0:
            i = 1
        elif i >= len(self.maze_path_points):
            i = len(self.maze_path_points) - 1

        #  Make a service call to get the points at the indices
        res1 = self.get_points_at_indices(i - 1, i)

        p1 = res1.point_a
        p2 = res1.point_b
        p3.x = trans[0]
        p3.y = trans[1]
        p3.z = trans[2]


        if p1.x == p2.x:
            if p3.y < min(p1.y, p2.y) or p3.y > max(p1.y, p2.y):
                return -1.0
        else:
            if p3.x < min(p1.x, p2.x) or p3.x > max(p1.x, p2.x):
                return -1.0

        numerator = abs((p2.y - p1.y) * p3.x - (p2.x - p1.x) * p3.y + p2.x*p1.y - p2.y*p1.x)
        denominator = math.sqrt(pow(p2.y - p1.y, 2.0) + pow(p2.x - p1.x, 2.0))

        distance = numerator / denominator

        return distance

    def spawn_new_coin(self):
        resp = self.spawn_new_coin_service(self.name, self.pointA_index, self.pointB_index)
        self.cur_coin_location = resp.coin_location
        self.coin_positions.append(self.cur_coin_location)
        print "Spawned new coin for " + self.name

    def remove_coin(self):
        resp = self.call_remove_coin_service(self.name)
        print "Coin removal " + resp.result

    def add_time(self):

        print_locations = {"trinculo": Point(1.65, 2.0, 0.01),
                           "miranda": Point(6.0, 2.0, 0.01),
                           "ferdinand": Point(1.65, 6.7, 0.01),
                           "prospero": Point(6.0, 6.7, 0.01)}

        rgb = self.rgb.get(self.name)

        self.time.header.frame_id = "map"
        self.time.header.stamp = rospy.Time(0)
        self.time.ns = self.name + "_timer"
        self.time.id = random.random()
        self.time.type = Marker.TEXT_VIEW_FACING
        self.time.action = Marker.ADD
        self.time.text = "Time: 120\nCoins:"\
                         + str(self.collected_coins) \
                         + "\nHealth: 100/100"
        self.time.pose.position = print_locations.get(self.name)
        self.time.pose.orientation.x = 0.0
        self.time.pose.orientation.y = 0.0
        self.time.pose.orientation.z = 0.0
        self.time.pose.orientation.w = 1.0
        self.time.scale.x = 1.0
        self.time.scale.y = 1.0
        self.time.scale.z = 0.3
        self.time.color.a = 1.0
        self.time.color.r = rgb[0]
        self.time.color.g = rgb[1]
        self.time.color.b = rgb[2]
        self.time_publisher.publish(self.time)

    def update_timer(self, event):
        if not self.started:
            return

        self.time.action = Marker.MODIFY
        self.time_remaining -= 1
        self.time.text = "Time: " + str(self.time_remaining) \
                         + "\nCoins: " + str(self.collected_coins) \
                         + "\nHealth: " + str(self.health) + "/100"
        self.time_publisher.publish(self.time)

    def remove_timer(self):
        self.time.action = Marker.DELETE
        self.time_publisher.publish(self.time)

    def robot_death_handler(self):
        self.isAlive = False
        self.t.shutdown()
        self.time.text = "DEAD"
        self.time_publisher.publish(self.time)
        self.send_to_home()

    def robot_win_handler(self):
        self.isAlive = False
        self.t.shutdown()
        self.time.text = "FINISHED!"
        self.time_publisher.publish(self.time)
        self.send_to_home()

    def send_to_home(self):

        home_locations = {"prospero": Pose(Point(6.73, 6.0, 0), Quaternion(*quaternion_from_euler(0, 0, math.pi))),
                          "miranda": Pose(Point(6.73, 2.8, 0), Quaternion(*quaternion_from_euler(0, 0, math.pi))),
                          "ferdinand": Pose(Point(2.1, 6.0, 0), Quaternion(*quaternion_from_euler(0, 0, 0))),
                          "trinculo": Pose(Point(2.1, 2.8, 0), Quaternion(*quaternion_from_euler(0, 0, 0)))}

        move_base = actionlib.SimpleActionClient("/" + self.name + "/move_base", MoveBaseAction)

        while not move_base.wait_for_server(rospy.Duration(5)):
            print "Waiting for action server...", self.name

        goal = MoveBaseGoal()
        goal.target_pose.header.frame_id = "map"
        goal.target_pose.header.stamp = rospy.Time.now()
        goal.target_pose.pose = home_locations.get(self.name)

        print "Sending " + self.name + " home."
        move_base.send_goal_and_wait(goal)

    def cleanup(self):
        self.remove_coin()
        self.remove_timer()

if __name__ == "__main__":
    rospy.init_node("robot_node")
    try:
        rbt = Robot()
    except rospy.ROSInterruptException:
        pass
