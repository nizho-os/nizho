# Builds both Qt programs for the RPM (packaging/nizho-convert.spec). The image
# build compiles each on its own, from its own folder.
cmake_minimum_required(VERSION 3.20)
project(nizho-convert LANGUAGES CXX)
add_subdirectory(wheel)
add_subdirectory(edit)
