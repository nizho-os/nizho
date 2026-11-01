cmake_minimum_required(VERSION 3.20)
project(nizho-wheel LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.6 REQUIRED COMPONENTS Gui Quick DBus)
find_package(KF6GlobalAccel REQUIRED)
find_package(KF6WindowSystem REQUIRED)
find_package(LayerShellQt REQUIRED)

qt_add_executable(nizho-wheel main.cpp)
qt_add_resources(nizho-wheel "qml" PREFIX "/" FILES Wheel.qml)
target_link_libraries(nizho-wheel PRIVATE Qt6::Gui Qt6::Quick Qt6::DBus
    KF6::GlobalAccel KF6::WindowSystem LayerShellQt::Interface)
install(TARGETS nizho-wheel DESTINATION bin)
