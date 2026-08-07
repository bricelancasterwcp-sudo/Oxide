fn is_pal(s: &str) -> bool {
    let rev: String = s.chars().rev().collect();
    rev == s
}

fn main() {
    for s in ["level", "stereo", "rotor"] {
        println!("{}", is_pal(s));
    }
}
