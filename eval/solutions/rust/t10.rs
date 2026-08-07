fn main() {
    let v = vec![4, 1, 7, 3, 9, 2];
    let kept: Vec<i64> = v.into_iter().filter(|&x| x > 3).collect();
    for x in &kept {
        println!("{}", x);
    }
    println!("{}", kept.len());
}
