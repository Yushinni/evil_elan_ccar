import os
import rospy
import numpy as np
import cv2
import tf
from cv_bridge import CvBridge, CvBridgeError
from sensor_msgs.msg import Image, NavSatFix, Imu, PointCloud2, CameraInfo
from can_msgs.msg import Frame
from sensor_msgs import point_cloud2
from std_msgs.msg import String
from radarParser import prettyhex2, Radar_Header_505, Radar_VehInfo_300, Radar_Sta_506, Radar_Target_A_508, Radar_Target_B_509

class SensorListener:
    def __init__(self):
        self.csv_directory = self.create_csv()
        self.radar_data = None
        self.No_Obj = 0
        self.bridge = CvBridge()
        self.image_rviz_pub = rospy.Publisher("/image", Image, queue_size=10)
        self.camera_info_pub = rospy.Publisher("/camera_info", CameraInfo, queue_size=10)
        self.image_pub = rospy.Publisher("/camera/image_raw", Image, queue_size=10)
        self.radar_pub = rospy.Publisher("/radar", String, queue_size=10)
        self.tf_broadcaster = tf.TransformBroadcaster()

        self.image_buffer = None
        self.radar_buffer = None
        self.radar_is_complete = False

    def create_csv(self):
        current_directory = os.getcwd()
        csv_directory = os.path.join(current_directory, "csv")
        if not os.path.exists(csv_directory):
            os.makedirs(csv_directory)
        with open(os.path.join(csv_directory, "radar_data.csv"), mode='w') as file:
            file.write("Timestamp,AEB_CIPVFlag,ACC_CIPVFlag,CIPVFlag,Vel_Y,Vel_X,Pos_Y,Pos_X,ID,Type,ProbExist,DynProp,MeasStat,Accel_X\n")
        with open(os.path.join(csv_directory, "navsatfix_data.csv"), mode='w') as file:
            file.write("Timestamp,Latitude,Longitude,Altitude,Covariance_0,Covariance_1,Covariance_2,Covariance_3,Covariance_4,Covariance_5,Covariance_6,Covariance_7,Covariance_8\n")
        with open(os.path.join(csv_directory, "imu_data.csv"), mode='w') as file:
            file.write("Timestamp,Orientation_x,Orientation_y,Orientation_z,Orientation_w,Angular_velocity_x,Angular_velocity_y,Angular_velocity_z,Linear_acceleration_x,Linear_acceleration_y,Linear_acceleration_z\n")
        with open(os.path.join(csv_directory, "lidar_data.csv"), mode='w') as file:
            file.write("Timestamp,x,y,z,intensity,ring\n")
        return csv_directory

    def publish_image(self, img):
        try:
            image_message = self.bridge.cv2_to_imgmsg(img, encoding="passthrough")
            image_message.header.frame_id = "camera_link"
            self.image_rviz_pub.publish(image_message)
        except CvBridgeError as e:
            print(e)

    def publish_camera_info(self):
        camera_info_msg = CameraInfo()
        camera_info_msg.width = 1280
        camera_info_msg.height = 720
        camera_info_msg.header.frame_id = "camera_link"
        camera_info_msg.K = [
            1.418667e+03, 0.000e+00, 6.4e+02,
            0.000e+00, 1.418667e+03, 3.6e+02,
            0.000e+00, 0.000e+00, 1.0e+00
        ]
        camera_info_msg.P = [
            1.418667e+03, 0.000e+00, 6.4e+02, 0.0e+00,
            0.000e+00, 1.418667e+03, 3.6e+02, 0.0e+00,
            0.000e+00, 0.000e+00, 1.0e+00, 0.0e+00
        ]
        self.camera_info_pub.publish(camera_info_msg)

    def broadcast_static_tf(self):
        self.tf_broadcaster.sendTransform((0, 0, 0), (0, 0, 0, 1), rospy.Time.now(), "camera_link", "world")

    def get_ros_timestamp(self, data):
        return data.header.stamp.to_sec()

    def can_callback(self, data, topic_name):
        timestamp = self.get_ros_timestamp(data)
        data_str = prettyhex2(data.dlc, data.data, '-').split('-')

        if topic_name == "/can0/received_msg":
            if data.id == 0x300:
                radar_veh_info = np.array(Radar_VehInfo_300(data_str))
            elif 0x505 <= data.id <= 0x547:
                rospy.loginfo(f"Received Radar data {data.id}")
                if data.id == 0x505:
                    self.No_Obj = (Radar_Header_505(data_str)[5]) * 2
                    self.radar_data = np.array([timestamp] + list(Radar_Header_505(data_str)))
                elif data.id == 0x506:
                    Radar_Sta_506(data_str)
                if self.No_Obj >= 0:
                    if self.radar_data is not None:
                        if 0x508 <= data.id <= 0x546 and data.id % 2 == 0:
                            self.radar_data = np.concatenate((self.radar_data, Radar_Target_A_508(data_str)[0:8]))
                            self.No_Obj -= 1
                        elif 0x509 <= data.id <= 0x547 and data.id % 2 == 1:
                            self.radar_data = np.concatenate((self.radar_data, Radar_Target_B_509(data_str)[0:5]))
                            self.No_Obj -= 1

                    ### Radar Data ###
                    if self.No_Obj == 0:
                        self.radar_buffer = self.radar_data
                        self.radar_pub.publish(','.join(map(str, self.radar_buffer)))
                        # self.check_and_output()

                        if (self.radar_data is not None):
                            file_path = os.path.join(self.csv_directory, "radar_data.csv")
                            with open(file_path, 'a') as file:
                                np.savetxt(file, [self.radar_data], delimiter=',', fmt='%.8f')

    def image_callback(self, data):
        rospy.loginfo("Received Image data")

        try:
            self.publish_camera_info()
            img = self.bridge.imgmsg_to_cv2(data, desired_encoding="passthrough")
            self.publish_image(img)

            self.image_buffer = img
            # self.check_and_output()

        except CvBridgeError as e:
            print(e)

    def fix_callback(self, data):
        rospy.loginfo("Received NavSatFix data")

        timestamp = self.get_ros_timestamp(data)
        latitude = data.latitude
        longitude = data.longitude
        altitude = data.altitude
        position_covariance = data.position_covariance
        navsatfix_data = np.array([latitude, longitude, altitude] + list(position_covariance))

        file_path = os.path.join(self.csv_directory, "navsatfix_data.csv")
        with open(file_path, 'a') as file:
            np.savetxt(file, [[timestamp] + list(navsatfix_data)], delimiter=',', fmt='%.8f')

    def imu_callback(self, data):
        rospy.loginfo("Received IMU data")

        timestamp = self.get_ros_timestamp(data)
        orientation = [
            data.orientation.x,
            data.orientation.y,
            data.orientation.z,
            data.orientation.w
        ]
        angular_velocity = [
            data.angular_velocity.x,
            data.angular_velocity.y,
            data.angular_velocity.z
        ]
        linear_acceleration = [
            data.linear_acceleration.x,
            data.linear_acceleration.y,
            data.linear_acceleration.z
        ]
        imu_data = np.array(orientation + angular_velocity + linear_acceleration)

        file_path = os.path.join(self.csv_directory, "imu_data.csv")
        with open(file_path, 'a') as file:
            np.savetxt(file, [[timestamp] + list(imu_data)], delimiter=',', fmt='%.8f')

    def pointcloud_callback(self, data):
        rospy.loginfo("Received PointCloud2 data")

        timestamp = self.get_ros_timestamp(data)
        gen = point_cloud2.read_points(data, field_names=("x", "y", "z", "intensity", "ring"), skip_nans=True)
        lidar_data = np.array(list(gen))

        file_path = os.path.join(self.csv_directory, "lidar_data.csv")
        with open(file_path, 'a') as file:
            np.savetxt(file, np.hstack((np.full((lidar_data.shape[0], 1), timestamp), lidar_data)), delimiter=',', fmt='%.8f')

    # def check_and_output(self):
    #     if ((self.image_buffer is not None) and (self.radar_buffer is not None)):
    #         rospy.loginfo(f"Image and Radar data are both available, Processing ...")

    #         if (self.radar_buffer is not None and self.image_buffer is not None):
                
    #             self.image_pub.publish(self.bridge.cv2_to_imgmsg(self.image_buffer, encoding="passthrough"))
    #             self.radar_pub.publish(','.join(map(str, self.radar_buffer)))

    #         self.image_buffer = None
    #         self.radar_buffer = None

    def listener(self):
        rospy.init_node("listener", anonymous=True)
        rospy.Subscriber("/can0/received_msg", Frame, self.can_callback, callback_args="/can0/received_msg")
        # rospy.Subscriber("/can1/received_msg", Frame, can_callback, callback_args="/can1/received_msg")
        # rospy.Subscriber("/can2/received_msg", Frame, can_callback, callback_args="/can2/received_msg")
        rospy.Subscriber("/cme_cam", Image, self.image_callback, queue_size=10)
        rospy.Subscriber("/fix", NavSatFix, self.fix_callback)
        rospy.Subscriber("/imu", Imu, self.imu_callback)
        rospy.Subscriber("/velodyne_points", PointCloud2, self.pointcloud_callback)
        rospy.Timer(rospy.Duration(1.0), lambda event: self.broadcast_static_tf())
        rospy.spin()

if __name__ == "__main__":
    listener = SensorListener()
    listener.listener()