# Standalone: nothing in the rest of the repo builds, and this builds nothing from the rest of it.
# The GUI only ever touches /opt/lgpowercontrol/lgpowercontrol.conf as a text file, so install.py
# and the Python package stay unaware of it.
cmake_minimum_required(VERSION 3.19)

project(lgpowercontrol-gui VERSION 1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 6.2 REQUIRED COMPONENTS Widgets)

add_executable(lgpowercontrol-gui
    main.cpp
    conf.cpp
)

target_link_libraries(lgpowercontrol-gui PRIVATE Qt6::Widgets)
target_compile_options(lgpowercontrol-gui PRIVATE -Wall -Wextra)

include(GNUInstallDirs)
install(TARGETS lgpowercontrol-gui RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(FILES lgpowercontrol-gui.desktop DESTINATION ${CMAKE_INSTALL_DATADIR}/applications)
