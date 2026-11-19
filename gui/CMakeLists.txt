cmake_minimum_required(VERSION 3.20)
# The GUI layer is implemented in Rust (src/gui.rs) and built via Cargo at the
# repository root. CMake keeps the gui/build/gamerecomp entrypoint working cleanly.
project(gamerecomp-gui NONE)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
find_program(CARGO_BIN cargo REQUIRED)

add_custom_target(gamerecomp ALL
    COMMAND "${CARGO_BIN}" build --release
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${REPO_ROOT}/target/release/gamerecomp"
        "${CMAKE_CURRENT_BINARY_DIR}/gamerecomp"
    WORKING_DIRECTORY "${REPO_ROOT}"
    COMMENT "Building Rust GUI and staging gui/build/gamerecomp"
)
