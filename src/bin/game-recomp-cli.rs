use std::env;

fn main() {
    let raw_args: Vec<String> = env::args().skip(1).collect();
    let code = game_recomp::cli::dispatch(&raw_args);
    std::process::exit(code);
}
