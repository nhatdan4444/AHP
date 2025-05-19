function restrictInput(input) {
    let value = parseFloat(input.value);
    if (value < 1 || value > 9) {
        alert("Giá trị phải từ 1 đến 9!");
        input.value = 1;
    }
}