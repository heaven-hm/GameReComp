cmake_minimum_required(VERSION 3.20)
# Builds both Rust binaries:
#   - game-recomp-cli (clap CLI)
#   - game-recomp-gui (GPUI desktop GUI)
# Also stages gui/build/gamerecomp for verify.sh compatibility.
project(game-recomp-gui NONE)

get_filename_component(REPO_ROOT "${CMAKE_CURRENT_SOURCE_DIR}/.." ABSOLUTE)
find_program(CARGO_BIN cargo REQUIRED)

add_custom_target(gamerecomp ALL
    COMMAND "${CARGO_BIN}" build --release
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${REPO_ROOT}/target/release/game-recomp-gui"
        "${CMAKE_CURRENT_BINARY_DIR}/game-recomp-gui"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${REPO_ROOT}/target/release/game-recomp-cli"
        "${CMAKE_CURRENT_BINARY_DIR}/game-recomp-cli"
    COMMAND "${CMAKE_COMMAND}" -E copy_if_different
        "${REPO_ROOT}/target/release/game-recomp-gui"
        "${CMAKE_CURRENT_BINARY_DIR}/gamerecomp"
    WORKING_DIRECTORY "${REPO_ROOT}"
    COMMENT "Building game-recomp-cli and game-recomp-gui"
)
