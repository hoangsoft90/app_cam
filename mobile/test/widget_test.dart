import 'package:camviet/main.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('FinancialAction.guard throws while offline', () {
    isOnline.value = false;
    expect(() => FinancialAction.guard(), throwsStateError);
  });

  test('FinancialAction.guard is a no-op while online', () {
    isOnline.value = true;
    expect(() => FinancialAction.guard(), returnsNormally);
  });

  test('idempotency keys are unique', () {
    final keys = {for (var i = 0; i < 100; i++) newIdempotencyKey()};
    expect(keys.length, 100);
  });
}
