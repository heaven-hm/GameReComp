cmake_minimum_required(VERSION 3.20)
# The GUI layer of Phase 5: a C++17 / Qt 6 Widgets application over the same
# backend as the CLI, not a second implementation of it. Every number it shows
# is read from a file the pipeline wrote; it never computes a status itself,
# because a GUI that derives "pass" from anything but the record is a GUI that
# can disagree with the CLI.
project(reveng-studio-gui LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
find_package(Qt6 REQUIRED COMPONENTS Widgets)
qt_standard_project_setup()
qt_add_executable(reveng-studio
    main.cpp
    projectmodel.cpp projectmodel.h
    mainwindow.cpp mainwindow.h
)
target_link_libraries(reveng-studio PRIVATE Qt6::Widgets)
set_target_properties(reveng-studio PROPERTIES MACOSX_BUNDLE FALSE)
