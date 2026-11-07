cmake_minimum_required(VERSION 3.20)
project(nizho-edit LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.6 REQUIRED COMPONENTS Gui Quick QuickControls2)

qt_add_executable(nizho-edit main.cpp)
qt_add_resources(nizho-edit "qml" PREFIX "/" FILES Edit.qml Player.qml)
target_link_libraries(nizho-edit PRIVATE Qt6::Gui Qt6::Quick Qt6::QuickControls2)
install(TARGETS nizho-edit DESTINATION bin)
