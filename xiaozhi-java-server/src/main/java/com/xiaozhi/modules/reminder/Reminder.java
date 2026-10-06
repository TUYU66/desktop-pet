package com.xiaozhi.modules.reminder;

/** 时间统一使用Unix毫秒，避免服务器时区改变倒计时；发送完成不等于用户已确认。 */
public record Reminder(String id, long userId, String deviceId, String title, String status,
                       long dueAt, long nextAttemptAt, long expiresAt, long version,
                       String attemptId, String lastError, long createdAt, long updatedAt, long initialSeconds,
                       String planId, int deliveryCount, long ackDeadline, long requestedAt,
                       String taskId, long originalDueAt, long scheduledAt) {
    public Reminder(String id,long userId,String deviceId,String title,String status,long dueAt,long nextAttemptAt,
                    long expiresAt,long version,String attemptId,String lastError,long createdAt,long updatedAt,long initialSeconds,
                    String planId,int deliveryCount,long ackDeadline,long requestedAt,String taskId,long originalDueAt) {
        this(id,userId,deviceId,title,status,dueAt,nextAttemptAt,expiresAt,version,attemptId,lastError,createdAt,updatedAt,initialSeconds,
             planId,deliveryCount,ackDeadline,requestedAt,taskId,originalDueAt,originalDueAt>0?originalDueAt:dueAt);
    }
    public Reminder(String id,long userId,String deviceId,String title,String status,long dueAt,long nextAttemptAt,
                    long expiresAt,long version,String attemptId,String lastError,long createdAt,long updatedAt,long initialSeconds,
                    String planId,int deliveryCount,long ackDeadline,long requestedAt) {
        this(id,userId,deviceId,title,status,dueAt,nextAttemptAt,expiresAt,version,attemptId,lastError,createdAt,updatedAt,initialSeconds,planId,deliveryCount,ackDeadline,requestedAt,null,requestedAt>0?requestedAt:dueAt);
    }
    public Reminder(String id,long userId,String deviceId,String title,String status,long dueAt,long nextAttemptAt,
                    long expiresAt,long version,String attemptId,String lastError,long createdAt,long updatedAt,long initialSeconds) {
        this(id,userId,deviceId,title,status,dueAt,nextAttemptAt,expiresAt,version,attemptId,lastError,createdAt,updatedAt,initialSeconds,null,0,0,0,null,dueAt);
    }
}
