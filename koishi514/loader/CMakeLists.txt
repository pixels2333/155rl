cmake_minimum_required(VERSION 3.20)
project(winmm_proxy LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_definitions(-D_CRT_SECURE_NO_WARNINGS -DWIN32_LEAN_AND_MEAN)
add_compile_options(/utf-8)

set(SOURCES
    src/dllmain.cpp
    src/proxy_winmm.cpp
    src/mod_loader.cpp
)

set(HEADERS
    src/proxy_winmm.h
    src/mod_loader.h
)

add_library(winmm SHARED ${SOURCES} ${HEADERS} winmm.def)

target_link_libraries(winmm PRIVATE shlwapi)

set_target_properties(winmm PROPERTIES
    OUTPUT_NAME "winmm"
    PREFIX ""
    SUFFIX ".dll"
)
