fn main() {
    let v = push(push(push(push(push(vec(), 3), 8), -2), 12), 7)
    let count = 0
    let max = -1000000
    for x in v {
        if x > 0 {
            count = count + 1
        }
        if x > max {
            max = x
        }
    }
    print(count)
    print(max)
}
